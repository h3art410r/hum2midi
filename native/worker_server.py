"""Official DiffSynth-Music Prosody Worker for the clean native version."""

from __future__ import annotations

import os
import threading
import time
import traceback
import uuid
from collections import deque
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
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
BUILD = os.getenv("H2M_WORKER_BUILD", "unknown")
WORK_DIR = Path(os.getenv("NATIVE_WORKER_DIR", "runtime/native_worker_jobs"))
WORK_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Hum2Midi Native DiffSynth Worker")
PIPE: DiffSynthMusicPipeline | None = None
TEMPLATE: TemplatePipeline | None = None
MODEL_READY = False
GENERATION_LOCK = threading.Lock()
WORKER_LOGS: deque[dict[str, object]] = deque(maxlen=500)
LOG_SEQ = 0


def _log(event: str, request_id: str | None = None, **fields: object) -> None:
    global LOG_SEQ
    LOG_SEQ += 1
    stamp = time.strftime("%H:%M:%S")
    message = f"[NATIVE-DIFFSYNTH] {stamp} {event}"
    if request_id:
        message += f" request={request_id}"
    if fields:
        message += " " + " ".join(f"{key}={value}" for key, value in fields.items())
    print(message, flush=True)
    WORKER_LOGS.append({
        "seq": LOG_SEQ,
        "time": stamp,
        "event": event,
        "request_id": request_id,
        "fields": {key: str(value) for key, value in fields.items()},
    })


def _vram_limit_gb() -> float:
    _free, total = torch.cuda.mem_get_info("cuda")
    return total / (1024**3) - 0.5


def _load_wav_without_torchcodec(path: Path, division_factor: int) -> torch.Tensor:
    """Read the backend's normalized PCM WAV when TorchCodec is unavailable.

    DiffSynth's official ``LoadMultiTrackAudio`` currently delegates to
    ``torchaudio.load`` and therefore requires the optional TorchCodec
    package. The model receives the same [channels, samples] tensor either
    way; this fallback only removes that optional decoder dependency.
    """
    data, sample_rate = sf.read(str(path), dtype="float32", always_2d=True)
    if sample_rate != 48000:
        raise RuntimeError(f"Native Worker expects 48000 Hz WAV, got {sample_rate}")
    waveform = torch.from_numpy(np.asarray(data.T, dtype=np.float32).copy())
    if waveform.shape[0] == 1:
        waveform = waveform.repeat(2, 1)
    elif waveform.shape[0] > 2:
        waveform = waveform[:2]
    length = (waveform.shape[1] // division_factor) * division_factor
    if length <= 0:
        raise RuntimeError("Input audio is shorter than the official division factor")
    return waveform[:, :length]


def _model_configs() -> list[ModelConfig]:
    """Official low-VRAM model-card configuration.

    The separator weights are loaded because they are part of the official
    pipeline definition, even though this humming-only entry point never
    invokes ``extract_track``.
    """
    disk_vram = {
        "offload_dtype": "disk",
        "offload_device": "disk",
        "onload_dtype": "disk",
        "onload_device": "disk",
        "preparing_dtype": torch.bfloat16,
        "preparing_device": "cuda",
        "computation_dtype": torch.bfloat16,
        "computation_device": "cuda",
    }
    cpu_vram = {
        "offload_dtype": torch.bfloat16,
        "offload_device": "cpu",
        "onload_dtype": torch.bfloat16,
        "onload_device": "cpu",
        "preparing_dtype": torch.bfloat16,
        "preparing_device": "cuda",
        "computation_dtype": torch.bfloat16,
        "computation_device": "cuda",
    }
    fp32_cpu_vram = {
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
        ModelConfig(model_id=MODEL_ID, origin_file_pattern="transformer/model.safetensors", **disk_vram),
        ModelConfig(model_id=MODEL_ID, origin_file_pattern="conditioner/model.safetensors", **disk_vram),
        ModelConfig(model_id=MODEL_ID, origin_file_pattern="text_encoder/model.safetensors", **disk_vram),
        ModelConfig(model_id=MODEL_ID, origin_file_pattern="vae/model.safetensors", **cpu_vram),
        ModelConfig(model_id=MODEL_ID, origin_file_pattern="track_separator/model.safetensors", **fp32_cpu_vram),
    ]


def _template_configs() -> list[ModelConfig]:
    # Keep official model_id numbering: control=0, prosody=1, reference=2.
    return [
        ModelConfig(model_id=MODEL_ID, origin_file_pattern="template_control/"),
        ModelConfig(model_id=MODEL_ID, origin_file_pattern="template_prosody/"),
        ModelConfig(model_id=MODEL_ID, origin_file_pattern="template_reference/"),
    ]


def load_models() -> None:
    global PIPE, TEMPLATE, MODEL_READY
    if MODEL_READY:
        return
    started = time.perf_counter()
    _log("MODEL_LOAD_START", model_id=MODEL_ID, build=BUILD)
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is unavailable; native DiffSynth Worker requires the GPU machine")
    PIPE = DiffSynthMusicPipeline.from_pretrained(
        torch_dtype=torch.bfloat16,
        device="cuda",
        model_configs=_model_configs(),
        tokenizer_config=ModelConfig(model_id=MODEL_ID, origin_file_pattern="text_encoder/"),
        vram_limit=_vram_limit_gb(),
    )
    TEMPLATE = TemplatePipeline.from_pretrained(
        torch_dtype=torch.bfloat16,
        device="cuda",
        model_configs=_template_configs(),
        lazy_loading=True,
    )
    MODEL_READY = True
    _log(
        "MODEL_LOAD_DONE",
        elapsed=f"{time.perf_counter() - started:.3f}s",
        execution="official_prosody_quick_start",
        vram_limit_gb=f"{_vram_limit_gb():.3f}",
        compute_dtype="bfloat16",
        templates="control,prosody,reference(lazy)",
    )


@app.on_event("startup")
def startup() -> None:
    load_models()


@app.get("/health")
def health() -> dict[str, object]:
    return {
        "status": "ok" if MODEL_READY else "starting",
        "provider": "DiffSynth-Music",
        "control": "prosody",
        "execution": "official_prosody_quick_start",
        "model_id": MODEL_ID,
        "build": BUILD,
        "device": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "none",
        "vram_limit_gb": _vram_limit_gb() if torch.cuda.is_available() else None,
        "compute_dtype": "bfloat16",
        "worker_busy": GENERATION_LOCK.locked(),
    }


@app.get("/debug/logs")
def debug_logs(since: int = 0, limit: int = 200, request_id: str = "") -> dict[str, object]:
    limit = max(1, min(limit, 500))
    entries = [
        item for item in WORKER_LOGS
        if int(item["seq"]) > since and (not request_id or item.get("request_id") == request_id)
    ]
    return {"cursor": LOG_SEQ, "logs": entries[-limit:]}


@app.post("/v1/generate")
def generate(
    audio: UploadFile = File(...),
    prompt: str = Form(...),
    seed: int = Form(42),
    cfg_scale: float = Form(4),
    steps: int = Form(50),
    control: str = Form("prosody"),
    authorization: str | None = Header(default=None),
) -> FileResponse:
    request_id = uuid.uuid4().hex[:12]
    started = time.perf_counter()
    if TOKEN and authorization != f"Bearer {TOKEN}":
        raise HTTPException(401, "Invalid worker token")
    if control != "prosody":
        raise HTTPException(400, "Native Worker only supports official Prosody conditioning")
    if not prompt.strip():
        raise HTTPException(400, "Prompt is required")
    if not GENERATION_LOCK.acquire(blocking=False):
        raise HTTPException(409, "Worker is busy; retry this generation")
    _log("REQUEST_START", request_id, prompt_chars=len(prompt), seed=seed, cfg=cfg_scale, steps=steps)
    input_path = WORK_DIR / f"{request_id}-input.wav"
    output_path = WORK_DIR / f"{request_id}-output.wav"
    try:
        load_models()
        assert PIPE is not None and TEMPLATE is not None
        payload = audio.file.read()
        input_path.write_bytes(payload)
        _log("INPUT_SAVED", request_id, bytes=len(payload), filename=audio.filename or "input.wav")
        conditioning_started = time.perf_counter()
        loader = LoadMultiTrackAudio(division_factor=3840)
        try:
            waveform = loader(str(input_path))
        except (ImportError, RuntimeError) as exc:
            if "torchcodec" not in str(exc).lower():
                raise
            _log("TORCHCODEC_UNAVAILABLE", request_id, fallback="soundfile", error=str(exc))
            waveform = _load_wav_without_torchcodec(input_path, division_factor=3840)
        if waveform is None:
            raise RuntimeError("Official LoadMultiTrackAudio returned no waveform")
        prosody = extract_prosody(waveform)
        duration = prosody.shape[1] / 48000
        conditioning_elapsed = time.perf_counter() - conditioning_started
        _log("PROSODY_READY", request_id, waveform_shape=tuple(waveform.shape), prosody_shape=tuple(prosody.shape), duration=f"{duration:.3f}s", elapsed=f"{conditioning_elapsed:.3f}s")
        if torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats()
            torch.cuda.synchronize()
        infer_started = time.perf_counter()
        _log("MODEL_INFER_START", request_id, execution="official_template_pipeline", model_id=1)
        result = TEMPLATE(
            PIPE,
            prompt=prompt,
            negative_prompt=PIPE.default_negative_prompt,
            lyrics="",
            duration=duration,
            seed=seed,
            tiled=True,
            cfg_scale=cfg_scale,
            num_inference_steps=steps,
            template_inputs=[{"model_id": 1, "audio": prosody}],
            negative_template_inputs=[{"model_id": 1, "audio": prosody}],
        )
        if torch.cuda.is_available():
            torch.cuda.synchronize()
        infer_elapsed = time.perf_counter() - infer_started
        peak_allocated = torch.cuda.max_memory_allocated() / 1024**3 if torch.cuda.is_available() else 0.0
        peak_reserved = torch.cuda.max_memory_reserved() / 1024**3 if torch.cuda.is_available() else 0.0
        _log("MODEL_INFER_DONE", request_id, elapsed=f"{infer_elapsed:.3f}s", result_shape=tuple(result.shape), peak_allocated_gb=f"{peak_allocated:.3f}", peak_reserved_gb=f"{peak_reserved:.3f}")
        save_started = time.perf_counter()
        sf.write(str(output_path), result.detach().float().cpu().numpy().T, 48000, subtype="PCM_16")
        save_elapsed = time.perf_counter() - save_started
        total_elapsed = time.perf_counter() - started
        _log("AUDIO_SAVE_DONE", request_id, elapsed=f"{save_elapsed:.3f}s", total=f"{total_elapsed:.3f}s")
        return FileResponse(
            output_path,
            media_type="audio/wav",
            filename="native-diffsynth-output.wav",
            headers={
                "X-DiffSynth-Request-Id": request_id,
                "X-DiffSynth-Model-Version": MODEL_ID,
                "X-DiffSynth-Negative-Prompt-Source": "pipe.default_negative_prompt",
                "X-DiffSynth-Conditioning-Seconds": f"{conditioning_elapsed:.3f}",
                "X-DiffSynth-Inference-Seconds": f"{infer_elapsed:.3f}",
                "X-DiffSynth-Save-Seconds": f"{save_elapsed:.3f}",
                "X-DiffSynth-Total-Seconds": f"{total_elapsed:.3f}",
                "X-DiffSynth-Peak-Allocated-GB": f"{peak_allocated:.3f}",
                "X-DiffSynth-Peak-Reserved-GB": f"{peak_reserved:.3f}",
            },
        )
    except HTTPException:
        raise
    except Exception as exc:
        _log("REQUEST_ERROR", request_id, error=f"{type(exc).__name__}: {exc}")
        traceback.print_exc()
        raise HTTPException(500, f"Native DiffSynth generation failed: {type(exc).__name__}: {exc}") from exc
    finally:
        GENERATION_LOCK.release()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT)
