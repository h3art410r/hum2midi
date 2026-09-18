"""Minimal Windows/CUDA HTTP worker for DiffSynth-Music Prosody control.

This module is intentionally isolated from the web app: the development
machine never imports DiffSynth or CUDA packages. Start it on the 5060Ti host
with scripts/start_diffsynth_music_server.ps1.
"""

from __future__ import annotations

import os
import tempfile
import time
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
    return [
        ModelConfig(model_id=MODEL_ID, origin_file_pattern="transformer/model.safetensors"),
        ModelConfig(model_id=MODEL_ID, origin_file_pattern="conditioner/model.safetensors"),
        ModelConfig(model_id=MODEL_ID, origin_file_pattern="text_encoder/model.safetensors"),
        ModelConfig(model_id=MODEL_ID, origin_file_pattern="vae/model.safetensors"),
        ModelConfig(model_id=MODEL_ID, origin_file_pattern="track_separator/model.safetensors", computation_dtype=torch.float32),
    ]


def load_models() -> None:
    global PIPE, TEMPLATE
    if PIPE is not None:
        return
    dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    PIPE = DiffSynthMusicPipeline.from_pretrained(
        torch_dtype=dtype, device="cuda", model_configs=_configs(),
        tokenizer_config=ModelConfig(model_id=MODEL_ID, origin_file_pattern="text_encoder/"),
    )
    TEMPLATE = TemplatePipeline.from_pretrained(
        torch_dtype=dtype, device="cuda",
        model_configs=[
            ModelConfig(model_id=MODEL_ID, origin_file_pattern="template_control/"),
            ModelConfig(model_id=MODEL_ID, origin_file_pattern="template_prosody/"),
            ModelConfig(model_id=MODEL_ID, origin_file_pattern="template_reference/"),
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
    authorization: str | None = Header(default=None),
) -> FileResponse:
    if TOKEN and authorization != f"Bearer {TOKEN}":
        raise HTTPException(401, "Invalid worker token")
    if control != "prosody":
        raise HTTPException(400, "This worker only exposes the Prosody control path")
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
        result = TEMPLATE(
            PIPE,
            prompt=prompt,
            negative_prompt=PIPE.default_negative_prompt,
            lyrics="",
            duration=seconds,
            seed=seed,
            tiled=True,
            cfg_scale=float(os.getenv("DIFFSYNTH_CFG_SCALE", "4")),
            num_inference_steps=int(os.getenv("DIFFSYNTH_STEPS", "50")),
            template_inputs=[{"model_id": 1, "audio": prosody}],
            negative_template_inputs=[{"model_id": 1, "audio": prosody}],
        )
        # Avoid torchaudio.save -> TorchCodec on newer torchaudio builds.
        sf.write(str(output_path), result.detach().float().cpu().numpy().T, 48000, subtype="PCM_16")
        print(f"generated seconds={seconds:.2f} elapsed={time.perf_counter() - started:.1f}s vram={torch.cuda.max_memory_allocated()/1024**3:.2f}GB", flush=True)
        return FileResponse(output_path, media_type="audio/wav", filename="diffsynth-output.wav")
    except Exception as exc:
        raise HTTPException(500, f"DiffSynth generation failed: {type(exc).__name__}: {exc}") from exc
    finally:
        # FileResponse reads lazily; cleanup is intentionally deferred by the
        # OS temp directory policy rather than deleting the output too early.
        pass


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT)
