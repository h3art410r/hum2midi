"""Minimal Windows/CUDA HTTP worker for DiffSynth-Music Prosody control.

This module is intentionally isolated from the web app: the development
machine never imports DiffSynth or CUDA packages. Start it on the 5060Ti host
with scripts/start_diffsynth_music_server.ps1.
"""

from __future__ import annotations

import os
import tempfile
import time
import traceback
import uuid
from pathlib import Path

import torch
import torchaudio
import soundfile as sf
from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse

from diffsynth.core.data.operators import LoadMultiTrackAudio
from diffsynth.diffusion.template import TemplatePipeline
from diffsynth.pipelines.diffsynth_music import DiffSynthMusicPipeline, ModelConfig
from diffsynth.utils.music_tools import extract_prosody


MODEL_ID = os.getenv("DIFFSYNTH_MODEL_ID", "DiffSynth-Studio/DiffSynth-Music")
HOST = os.getenv("DIFFSYNTH_SERVER_HOST", "0.0.0.0")
PORT = int(os.getenv("DIFFSYNTH_SERVER_PORT", "8765"))
TOKEN = os.getenv("DIFFSYNTH_REMOTE_TOKEN", "")
WORK_DIR = Path(os.getenv("DIFFSYNTH_WORK_DIR", "runtime/diffsynth_jobs"))
WORK_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="DiffSynth-Music Prosody Worker")
PIPE = None
TEMPLATE = None


def _worker_log(event: str, request_id: str | None = None, **fields: object) -> None:
    """Emit compact, grep-friendly timings for the remote worker console."""
    stamp = time.strftime("%H:%M:%S")
    prefix = f"[DIFFSYNTH] {stamp} {event}"
    if request_id:
        prefix += f" request={request_id}"
    details = " ".join(f"{key}={value}" for key, value in fields.items())
    print(f"{prefix} {details}".rstrip(), flush=True)


def _configs():
    vram_config = {
        "offload_dtype": "disk",
        "offload_device": "disk",
        "onload_dtype": "disk",
        "onload_device": "disk",
        "preparing_dtype": torch.bfloat16,
        "preparing_device": "cuda",
        "computation_dtype": torch.bfloat16,
        "computation_device": "cuda",
    }
    vram_config_cpu = {
        "offload_dtype": torch.bfloat16,
        "offload_device": "cpu",
        "onload_dtype": torch.bfloat16,
        "onload_device": "cpu",
        "preparing_dtype": torch.bfloat16,
        "preparing_device": "cuda",
        "computation_dtype": torch.bfloat16,
        "computation_device": "cuda",
    }
    vram_config_fp32 = {
        "offload_dtype": torch.float32,
        "offload_device": "cpu",
        "onload_dtype": torch.float32,
        "onload_device": "cpu",
        "preparing_dtype": torch.float32,
        "preparing_device": "cuda",
        "computation_dtype": torch.float32,
        "computation_device": "cuda",
    }
    return [
        # Disk offload keeps VRAM low, but DiffSynth's Windows execution path
        # deep-copies disk-backed modules during inference. The safetensors
        # handles are not deepcopy/pickle-safe, so use CPU offload for these
        # three modules on Windows and keep only the active layer on CUDA.
        ModelConfig(model_id=MODEL_ID, origin_file_pattern="transformer/model.safetensors", **vram_config_cpu),
        ModelConfig(model_id=MODEL_ID, origin_file_pattern="conditioner/model.safetensors", **vram_config_cpu),
        ModelConfig(model_id=MODEL_ID, origin_file_pattern="text_encoder/model.safetensors", **vram_config_cpu),
        ModelConfig(model_id=MODEL_ID, origin_file_pattern="vae/model.safetensors", **vram_config_cpu),
        ModelConfig(model_id=MODEL_ID, origin_file_pattern="track_separator/model.safetensors", **vram_config_fp32),
    ]


def load_models() -> None:
    global PIPE, TEMPLATE
    if PIPE is not None:
        return
    started = time.perf_counter()
    _worker_log("MODEL_LOAD_START", model_id=MODEL_ID)
    dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    PIPE = DiffSynthMusicPipeline.from_pretrained(
        torch_dtype=dtype, device="cuda", model_configs=_configs(),
        tokenizer_config=ModelConfig(model_id=MODEL_ID, origin_file_pattern="text_encoder/"),
        vram_limit=torch.cuda.mem_get_info("cuda")[1] / (1024 ** 3) - 0.5,
    )
    TEMPLATE = TemplatePipeline.from_pretrained(
        torch_dtype=dtype, device="cuda",
        model_configs=[
            ModelConfig(model_id=MODEL_ID, origin_file_pattern="template_control/"),
            ModelConfig(model_id=MODEL_ID, origin_file_pattern="template_prosody/"),
        ],
    )
    _worker_log(
        "MODEL_LOAD_DONE",
        elapsed=f"{time.perf_counter() - started:.3f}s",
        dtype=dtype,
        vram_total_gb=f"{torch.cuda.mem_get_info('cuda')[1] / (1024 ** 3):.2f}",
        templates="control,prosody",
    )


@app.on_event("startup")
def startup() -> None:
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is unavailable; this worker must run on the 5060Ti machine")
    load_models()


@app.get("/health")
def health() -> dict[str, object]:
    return {
        "status": "ok" if PIPE is not None else "starting",
        "provider": "DiffSynth-Music",
        "control": "control+prosody",
        "templates": ["control", "prosody"],
        "device": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "none",
        "model_id": MODEL_ID,
    }


@app.post("/v1/generate")
async def generate(
    audio: UploadFile = File(...),
    prompt: str = Form(...),
    duration: str = Form(""),
    seed: int = Form(101),
    control: str = Form("prosody"),
    control_profile: str = Form("control_prosody"),
    denoising_strength: str = Form(""),
    cfg_scale: str = Form(""),
    steps: str = Form(""),
    authorization: str | None = Header(default=None),
) -> FileResponse:
    request_id = uuid.uuid4().hex[:10]
    request_started = time.perf_counter()
    _worker_log(
        "REQUEST_START",
        request_id,
        filename=audio.filename or "input.wav",
        control_profile=control_profile,
        cfg=cfg_scale or os.getenv("DIFFSYNTH_CFG_SCALE", "4"),
        steps=steps or os.getenv("DIFFSYNTH_STEPS", "10"),
        seed=seed,
        denoise=denoising_strength or "none",
        duration=duration or "auto",
        prompt_chars=len(prompt),
    )
    if TOKEN and authorization != f"Bearer {TOKEN}":
        raise HTTPException(401, "Invalid worker token")
    if control != "prosody":
        raise HTTPException(400, "This worker currently uses the prosody endpoint")
    if control_profile not in {"prosody", "control_prosody", "anchored_low", "anchored_medium"}:
        raise HTTPException(400, "Unknown control_profile")
    load_models()
    suffix = Path(audio.filename or "input.wav").suffix or ".wav"
    input_path = Path(tempfile.mkstemp(prefix="input-", suffix=suffix, dir=WORK_DIR)[1])
    output_path = input_path.with_name(input_path.stem + "-output.wav")
    try:
        read_started = time.perf_counter()
        payload = await audio.read()
        input_path.write_bytes(payload)
        _worker_log(
            "INPUT_SAVED",
            request_id,
            bytes=len(payload),
            elapsed=f"{time.perf_counter() - read_started:.3f}s",
        )
        # Read the normalized WAV with soundfile. New torchaudio releases
        # route load() through TorchCodec, which is unnecessary for WAV and
        # makes the worker harder to deploy on Windows.
        decode_started = time.perf_counter()
        samples, sample_rate = sf.read(str(input_path), always_2d=True, dtype="float32")
        waveform = torch.from_numpy(samples.T.copy())
        _worker_log(
            "INPUT_DECODED",
            request_id,
            sample_rate=sample_rate,
            channels=waveform.shape[0],
            samples=waveform.shape[1],
            elapsed=f"{time.perf_counter() - decode_started:.3f}s",
        )
        if sample_rate != 48000:
            resample_started = time.perf_counter()
            waveform = torchaudio.functional.resample(waveform, sample_rate, 48000)
            _worker_log(
                "INPUT_RESAMPLED",
                request_id,
                from_rate=sample_rate,
                to_rate=48000,
                samples=waveform.shape[1],
                elapsed=f"{time.perf_counter() - resample_started:.3f}s",
            )
        # Control was trained on stereo vocal inputs; Prosody extraction is
        # intentionally mono. Keep both representations instead of feeding a
        # mono tensor into the Control adapter.
        prep_started = time.perf_counter()
        control_audio = waveform
        prosody = extract_prosody(waveform.mean(dim=0, keepdim=True))
        seconds = float(duration) if duration.strip() else prosody.shape[1] / 48000
        denoise = float(denoising_strength) if denoising_strength.strip() else None
        _worker_log(
            "CONDITIONING_READY",
            request_id,
            duration=f"{seconds:.3f}s",
            control_shape=tuple(control_audio.shape),
            prosody_shape=tuple(prosody.shape),
            denoise=denoise if denoise is not None else "none",
            elapsed=f"{time.perf_counter() - prep_started:.3f}s",
        )
        if torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats()
            torch.cuda.synchronize()
        infer_started = time.perf_counter()
        _worker_log(
            "MODEL_INFER_START",
            request_id,
            cfg=float(cfg_scale) if cfg_scale.strip() else float(os.getenv("DIFFSYNTH_CFG_SCALE", "4")),
            steps=int(steps) if steps.strip() else int(os.getenv("DIFFSYNTH_STEPS", "10")),
            templates="control,prosody" if control_profile != "prosody" else "prosody",
        )
        result = TEMPLATE(
            PIPE,
            prompt=prompt,
            negative_prompt=PIPE.default_negative_prompt,
            lyrics="",
            duration=seconds,
            seed=seed,
            tiled=True,
            cfg_scale=float(cfg_scale) if cfg_scale.strip() else float(os.getenv("DIFFSYNTH_CFG_SCALE", "4")),
            num_inference_steps=int(steps) if steps.strip() else int(os.getenv("DIFFSYNTH_STEPS", "10")),
            # Template model IDs follow the official DiffSynth-Music layout:
            # 0 = Control (vocal onset/rhythm), 1 = Prosody (pitch/timing).
            template_inputs=(
                [{"model_id": 0, "audio": control_audio}, {"model_id": 1, "audio": prosody}]
                if control_profile != "prosody"
                else [{"model_id": 1, "audio": prosody}]
            ),
            negative_template_inputs=(
                [{"model_id": 0, "audio": control_audio}, {"model_id": 1, "audio": prosody}]
                if control_profile != "prosody"
                else [{"model_id": 1, "audio": prosody}]
            ),
            input_audio=control_audio if denoise is not None else None,
            denoising_strength=denoise if denoise is not None else 1.0,
        )
        if torch.cuda.is_available():
            torch.cuda.synchronize()
        infer_elapsed = time.perf_counter() - infer_started
        peak_allocated = torch.cuda.max_memory_allocated() / 1024**3 if torch.cuda.is_available() else 0
        peak_reserved = torch.cuda.max_memory_reserved() / 1024**3 if torch.cuda.is_available() else 0
        _worker_log(
            "MODEL_INFER_DONE",
            request_id,
            elapsed=f"{infer_elapsed:.3f}s",
            result_shape=tuple(result.shape),
            peak_allocated_gb=f"{peak_allocated:.2f}",
            peak_reserved_gb=f"{peak_reserved:.2f}",
        )
        # Avoid torchaudio.save -> TorchCodec on newer torchaudio builds.
        save_started = time.perf_counter()
        sf.write(str(output_path), result.detach().float().cpu().numpy().T, 48000, subtype="PCM_16")
        _worker_log(
            "AUDIO_SAVE_DONE",
            request_id,
            bytes=output_path.stat().st_size,
            elapsed=f"{time.perf_counter() - save_started:.3f}s",
            total=f"{time.perf_counter() - request_started:.3f}s",
        )
        return FileResponse(output_path, media_type="audio/wav", filename="diffsynth-output.wav")
    except Exception as exc:
        _worker_log(
            "REQUEST_ERROR",
            request_id,
            elapsed=f"{time.perf_counter() - request_started:.3f}s",
            error=f"{type(exc).__name__}: {exc}",
        )
        traceback.print_exc()
        raise HTTPException(500, f"DiffSynth generation failed: {type(exc).__name__}: {exc}") from exc
    finally:
        # FileResponse reads lazily; cleanup is intentionally deferred by the
        # OS temp directory policy rather than deleting the output too early.
        pass


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT)
