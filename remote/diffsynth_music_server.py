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
    dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    PIPE = DiffSynthMusicPipeline.from_pretrained(
        torch_dtype=dtype, device="cuda", model_configs=_configs(),
        tokenizer_config=ModelConfig(model_id=MODEL_ID, origin_file_pattern="text_encoder/"),
        vram_limit=torch.cuda.mem_get_info("cuda")[1] / (1024 ** 3) - 0.5,
    )
    TEMPLATE = TemplatePipeline.from_pretrained(
        torch_dtype=dtype, device="cuda",
        model_configs=[
            ModelConfig(model_id=MODEL_ID, origin_file_pattern="template_prosody/"),
        ],
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
        "control": "prosody",
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
        input_path.write_bytes(await audio.read())
        # Read the normalized WAV with soundfile. New torchaudio releases
        # route load() through TorchCodec, which is unnecessary for WAV and
        # makes the worker harder to deploy on Windows.
        samples, sample_rate = sf.read(str(input_path), always_2d=True, dtype="float32")
        waveform = torch.from_numpy(samples.T.copy())
        if sample_rate != 48000:
            waveform = torchaudio.functional.resample(waveform, sample_rate, 48000)
        waveform = waveform.mean(dim=0, keepdim=True)
        prosody = extract_prosody(waveform)
        seconds = float(duration) if duration.strip() else prosody.shape[1] / 48000
        started = time.perf_counter()
        denoise = float(denoising_strength) if denoising_strength.strip() else None
        result = TEMPLATE(
            PIPE,
            prompt=prompt,
            negative_prompt=PIPE.default_negative_prompt,
            lyrics="",
            duration=seconds,
            seed=seed,
            tiled=True,
            cfg_scale=float(cfg_scale) if cfg_scale.strip() else float(os.getenv("DIFFSYNTH_CFG_SCALE", "4")),
            num_inference_steps=int(steps) if steps.strip() else int(os.getenv("DIFFSYNTH_STEPS", "50")),
            # Only template_prosody is resident on this 16GB GPU.
            template_inputs=[{"model_id": 0, "audio": prosody}],
            negative_template_inputs=[{"model_id": 0, "audio": prosody}],
            input_audio=waveform if denoise is not None else None,
            denoising_strength=denoise if denoise is not None else 1.0,
        )
        # Avoid torchaudio.save -> TorchCodec on newer torchaudio builds.
        sf.write(str(output_path), result.detach().float().cpu().numpy().T, 48000, subtype="PCM_16")
        print(f"generated seconds={seconds:.2f} elapsed={time.perf_counter() - started:.1f}s vram={torch.cuda.max_memory_allocated()/1024**3:.2f}GB", flush=True)
        return FileResponse(output_path, media_type="audio/wav", filename="diffsynth-output.wav")
    except Exception as exc:
        traceback.print_exc()
        raise HTTPException(500, f"DiffSynth generation failed: {type(exc).__name__}: {exc}") from exc
    finally:
        # FileResponse reads lazily; cleanup is intentionally deferred by the
        # OS temp directory policy rather than deleting the output too early.
        pass


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT)
