"""HTTP worker using the official DiffSynth-Music inference path.

The worker is isolated from the FastAPI application and runs on the CUDA
machine. Model loading and generation follow the official DiffSynth-Music
model-card example: the base pipeline uses the documented VRAM manager and
``TemplatePipeline`` receives the control/prosody template inputs directly.
There is no custom denoising anchor, KV-cache rewrite, layer paging wrapper,
or monkey-patched pipeline execution.
"""

from __future__ import annotations

import os
import tempfile
import time
import traceback
import uuid
from collections import deque
from pathlib import Path

import soundfile as sf
import torch
import torchaudio
from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse

from diffsynth.diffusion.template import TemplatePipeline
from diffsynth.pipelines.diffsynth_music import DiffSynthMusicPipeline, ModelConfig
from diffsynth.utils.music_tools import extract_prosody


MODEL_ID = os.getenv("DIFFSYNTH_MODEL_ID", "DiffSynth-Studio/DiffSynth-Music")
HOST = os.getenv("DIFFSYNTH_SERVER_HOST", "0.0.0.0")
PORT = int(os.getenv("DIFFSYNTH_SERVER_PORT", "8765"))
TOKEN = os.getenv("DIFFSYNTH_REMOTE_TOKEN", "")
WORK_DIR = Path(os.getenv("DIFFSYNTH_WORK_DIR", "runtime/diffsynth_jobs"))
WORK_DIR.mkdir(parents=True, exist_ok=True)
WORKER_BUILD = os.getenv("H2M_WORKER_BUILD", "unknown")

app = FastAPI(title="DiffSynth-Music Worker")
PIPE: DiffSynthMusicPipeline | None = None
TEMPLATE: TemplatePipeline | None = None
WORKER_LOGS: deque[dict[str, object]] = deque(maxlen=500)
WORKER_LOG_SEQ = 0


def _worker_log(event: str, request_id: str | None = None, **fields: object) -> None:
    """Keep a small request log for the development UI and worker console."""
    global WORKER_LOG_SEQ
    WORKER_LOG_SEQ += 1
    stamp = time.strftime("%H:%M:%S")
    prefix = f"[DIFFSYNTH] {stamp} {event}"
    if request_id:
        prefix += f" request={request_id}"
    details = " ".join(f"{key}={value}" for key, value in fields.items())
    print(f"{prefix} {details}".rstrip(), flush=True)
    WORKER_LOGS.append(
        {
            "seq": WORKER_LOG_SEQ,
            "time": stamp,
            "event": event,
            "request_id": request_id,
            "fields": {key: str(value) for key, value in fields.items()},
        }
    )


def _official_model_configs() -> list[ModelConfig]:
    """Return the VRAM configurations from the official model-card example."""
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
        ModelConfig(model_id=MODEL_ID, origin_file_pattern="transformer/model.safetensors", **vram_config),
        ModelConfig(model_id=MODEL_ID, origin_file_pattern="conditioner/model.safetensors", **vram_config),
        ModelConfig(model_id=MODEL_ID, origin_file_pattern="text_encoder/model.safetensors", **vram_config),
        ModelConfig(model_id=MODEL_ID, origin_file_pattern="vae/model.safetensors", **vram_config_cpu),
        ModelConfig(model_id=MODEL_ID, origin_file_pattern="track_separator/model.safetensors", **vram_config_fp32),
    ]


def _official_template_configs() -> list[ModelConfig]:
    return [
        ModelConfig(model_id=MODEL_ID, origin_file_pattern="template_control/"),
        ModelConfig(model_id=MODEL_ID, origin_file_pattern="template_prosody/"),
        ModelConfig(model_id=MODEL_ID, origin_file_pattern="template_reference/"),
    ]


def _vram_limit_gb() -> float:
    """Use the official half-gigabyte safety margin for the VRAM manager."""
    _free, total = torch.cuda.mem_get_info("cuda")
    return total / (1024**3) - 0.5


def load_models() -> None:
    global PIPE, TEMPLATE
    if PIPE is not None and TEMPLATE is not None:
        return
    started = time.perf_counter()
    _worker_log("MODEL_LOAD_START", model_id=MODEL_ID)
    # The official example uses BF16 on CUDA and the documented low-VRAM
    # model configurations. Do not silently switch to another execution path.
    dtype = torch.bfloat16
    PIPE = DiffSynthMusicPipeline.from_pretrained(
        torch_dtype=dtype,
        device="cuda",
        model_configs=_official_model_configs(),
        tokenizer_config=ModelConfig(model_id=MODEL_ID, origin_file_pattern="text_encoder/"),
        vram_limit=_vram_limit_gb(),
    )
    TEMPLATE = TemplatePipeline.from_pretrained(
        torch_dtype=dtype,
        device="cuda",
        model_configs=_official_template_configs(),
        lazy_loading=True,
    )
    _worker_log(
        "MODEL_LOAD_DONE",
        elapsed=f"{time.perf_counter() - started:.3f}s",
        dtype=str(dtype),
        vram_limit_gb=f"{_vram_limit_gb():.3f}",
        templates="control,prosody,reference",
        execution="official_template_pipeline",
    )


@app.on_event("startup")
def startup() -> None:
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is unavailable; this worker must run on the CUDA machine")
    load_models()


@app.get("/health")
def health() -> dict[str, object]:
    return {
        "status": "ok" if PIPE is not None and TEMPLATE is not None else "starting",
        "provider": "DiffSynth-Music",
        "control": "prosody",
        "templates": ["control", "prosody", "reference"],
        "execution": "official_model_card",
        "vram_limit_gb": _vram_limit_gb() if torch.cuda.is_available() else None,
        "build": WORKER_BUILD,
        "device": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "none",
        "model_id": MODEL_ID,
    }


@app.get("/debug/logs")
def debug_logs(since: int = 0, limit: int = 200, request_id: str = "") -> dict[str, object]:
    limit = max(1, min(limit, 500))
    events = [
        entry
        for entry in WORKER_LOGS
        if int(entry["seq"]) > since and (not request_id or entry.get("request_id") == request_id)
    ]
    return {"cursor": WORKER_LOG_SEQ, "logs": events[-limit:]}


@app.post("/v1/generate")
def generate(
    audio: UploadFile = File(...),
    prompt: str = Form(...),
    duration: str = Form(""),
    seed: int = Form(101),
    control: str = Form("prosody"),
    control_profile: str = Form("prosody"),
    cfg_scale: str = Form(""),
    steps: str = Form(""),
    authorization: str | None = Header(default=None),
) -> FileResponse:
    """Generate audio through the official TemplatePipeline call."""
    request_id = uuid.uuid4().hex[:10]
    request_started = time.perf_counter()
    _worker_log(
        "REQUEST_START",
        request_id,
        filename=audio.filename or "input.wav",
        control_profile=control_profile,
        cfg=cfg_scale or os.getenv("DIFFSYNTH_CFG_SCALE", "4"),
        steps=steps or os.getenv("DIFFSYNTH_STEPS", "50"),
        seed=seed,
        duration=duration or "auto",
        prompt_chars=len(prompt),
    )
    if TOKEN and authorization != f"Bearer {TOKEN}":
        raise HTTPException(401, "Invalid worker token")
    if control != "prosody":
        raise HTTPException(400, "This worker uses the official prosody conditioning path")
    if control_profile != "prosody":
        raise HTTPException(400, "This worker follows the official Prosody example")
    load_models()
    assert PIPE is not None and TEMPLATE is not None

    suffix = Path(audio.filename or "input.wav").suffix or ".wav"
    fd, input_name = tempfile.mkstemp(prefix="input-", suffix=suffix, dir=WORK_DIR)
    os.close(fd)
    input_path = Path(input_name)
    output_path = input_path.with_name(input_path.stem + "-output.wav")
    try:
        payload = audio.file.read()
        input_path.write_bytes(payload)
        _worker_log("INPUT_SAVED", request_id, bytes=len(payload))

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

        # The official Prosody example extracts a mono prosody condition and
        # passes only template model 1 to TemplatePipeline.
        prosody = extract_prosody(waveform.mean(dim=0, keepdim=True))
        seconds = float(duration) if duration.strip() else prosody.shape[1] / 48000
        cfg = float(cfg_scale) if cfg_scale.strip() else float(os.getenv("DIFFSYNTH_CFG_SCALE", "4"))
        inference_steps = int(steps) if steps.strip() else int(os.getenv("DIFFSYNTH_STEPS", "50"))
        template_inputs = [{"model_id": 1, "audio": prosody}]
        negative_template_inputs = [dict(item) for item in template_inputs]
        _worker_log(
            "CONDITIONING_READY",
            request_id,
            duration=f"{seconds:.3f}s",
            prosody_shape=tuple(prosody.shape),
            templates=",".join(str(item["model_id"]) for item in template_inputs),
        )

        if torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats()
            torch.cuda.synchronize()
        infer_started = time.perf_counter()
        _worker_log(
            "MODEL_INFER_START",
            request_id,
            cfg=cfg,
            steps=inference_steps,
            execution="official_template_pipeline",
        )
        result = TEMPLATE(
            PIPE,
            prompt=prompt,
            negative_prompt=PIPE.default_negative_prompt,
            lyrics="",
            duration=seconds,
            seed=seed,
            tiled=True,
            cfg_scale=cfg,
            num_inference_steps=inference_steps,
            template_inputs=template_inputs,
            negative_template_inputs=negative_template_inputs,
        )
        if torch.cuda.is_available():
            torch.cuda.synchronize()
        infer_elapsed = time.perf_counter() - infer_started
        peak_allocated = torch.cuda.max_memory_allocated() / 1024**3 if torch.cuda.is_available() else 0.0
        peak_reserved = torch.cuda.max_memory_reserved() / 1024**3 if torch.cuda.is_available() else 0.0
        _worker_log(
            "MODEL_INFER_DONE",
            request_id,
            elapsed=f"{infer_elapsed:.3f}s",
            result_shape=tuple(result.shape),
            peak_allocated_gb=f"{peak_allocated:.2f}",
            peak_reserved_gb=f"{peak_reserved:.2f}",
        )

        save_started = time.perf_counter()
        sf.write(str(output_path), result.detach().float().cpu().numpy().T, 48000, subtype="PCM_16")
        save_elapsed = time.perf_counter() - save_started
        total_elapsed = time.perf_counter() - request_started
        _worker_log(
            "AUDIO_SAVE_DONE",
            request_id,
            bytes=output_path.stat().st_size,
            elapsed=f"{save_elapsed:.3f}s",
            total=f"{total_elapsed:.3f}s",
        )
        return FileResponse(
            output_path,
            media_type="audio/wav",
            filename="diffsynth-output.wav",
            headers={
                "X-DiffSynth-Request-Id": request_id,
                "X-DiffSynth-Conditioning-Seconds": "0",
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
        _worker_log(
            "REQUEST_ERROR",
            request_id,
            elapsed=f"{time.perf_counter() - request_started:.3f}s",
            error=f"{type(exc).__name__}: {exc}",
        )
        traceback.print_exc()
        raise HTTPException(500, f"DiffSynth generation failed: {type(exc).__name__}: {exc}") from exc


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=HOST, port=PORT)
