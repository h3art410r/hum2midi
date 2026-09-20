from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any

import imageio_ffmpeg
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.stable_audio import (
    PROMPT_PLANS,
    StableAudioClient,
    StableAudioError,
)
from app.diffsynth_remote import DiffSynthRemoteClient, DiffSynthRemoteError

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT.parent / ".env")
STATIC = ROOT / "static"
DATA = Path(os.getenv("H2M_DATA_DIR", "data/demo"))
if not DATA.is_absolute():
    DATA = ROOT.parent / DATA
DATA = DATA.resolve()
DATA.mkdir(parents=True, exist_ok=True)
MAX_UPLOAD = int(os.getenv("MAX_UPLOAD_MB", "10")) * 1024 * 1024
JOBS: dict[str, dict[str, Any]] = {}
BACKEND_LOGS: deque[dict[str, Any]] = deque(maxlen=400)
BACKEND_LOG_SEQ = 0
app = FastAPI(title="Make Anything Musical Demo")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def _backend_log(message: str, *, job_id: str | None = None, level: str = "INFO") -> None:
    """Keep a small, safe in-memory log stream for the desktop debug view."""
    global BACKEND_LOG_SEQ
    BACKEND_LOG_SEQ += 1
    entry = {
        "seq": BACKEND_LOG_SEQ,
        "time": time.strftime("%H:%M:%S"),
        "level": level,
        "message": message,
    }
    if job_id:
        entry["job_id"] = job_id
    BACKEND_LOGS.append(entry)
    logger.info("backend_debug %s", entry)


@app.get("/api/debug/logs")
async def get_debug_logs(since: int = 0, limit: int = 100) -> dict[str, Any]:
    """Return recent backend events for local/demo troubleshooting."""
    limit = max(1, min(limit, 200))
    events = [entry for entry in BACKEND_LOGS if entry["seq"] > since]
    return {"cursor": BACKEND_LOG_SEQ, "logs": events[-limit:]}


@app.get("/", response_class=HTMLResponse)
async def home() -> FileResponse:
    return FileResponse(
        STATIC / "index.html",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )


@app.get("/api/health")
async def health() -> dict[str, Any]:
    provider = _make_provider()
    if isinstance(provider, DiffSynthRemoteClient):
        remote = provider.health()
        return {"status": "ok" if remote.get("status") == "ok" else "degraded", "provider": provider.status(), "runtime": "remote-cuda", "remote": remote}
    return {
        "status": "ok" if provider.script.is_file() else "degraded",
        "provider": provider.status(),
        "runtime": "local-tflite",
    }


@app.get("/api/prompt-presets")
async def prompt_presets() -> dict[str, Any]:
    """Expose five vertical transformation plans and human translations."""
    return {
        "plans": [
            {
                "id": plan_id,
                "name": plan["name"],
                "description": plan["description"],
                "noise": plan["noise"],
                "prompt": plan["prompt"],
                "translation": plan["translation"],
            }
            for plan_id, plan in PROMPT_PLANS.items()
        ],
    }


@app.post("/api/generations", status_code=202)
async def create_generation(request: Request) -> JSONResponse:
    _backend_log(
        f"POST /api/generations received content_type={request.headers.get('content-type', '')}"
    )
    audio = await request.body()
    if not audio:
        _backend_log("upload rejected: empty request", level="ERROR")
        raise HTTPException(400, "No audio data received")
    if len(audio) > MAX_UPLOAD:
        _backend_log(f"upload rejected: {len(audio)} bytes exceeds limit", level="ERROR")
        raise HTTPException(413, f"Audio exceeds {MAX_UPLOAD // (1024 * 1024)} MB")

    filename = request.headers.get("x-audio-filename", "hum.m4a")
    requested_preset = request.headers.get("x-style-preset", "").strip()
    control_profile = request.headers.get("x-diffsynth-control-profile", "prosody").strip()
    cfg_scale = request.headers.get("x-diffsynth-cfg-scale", "4").strip()
    steps = request.headers.get("x-diffsynth-steps", "50").strip()
    seed = request.headers.get("x-diffsynth-seed", "101").strip()
    if requested_preset:
        _backend_log(
            f"legacy preset header ignored; generating all vertical plans requested={requested_preset}",
            level="WARN",
        )
    suffix = Path(filename).suffix.lower()
    content_type = request.headers.get("content-type", "").lower()
    if "webm" in content_type or suffix == ".webm":
        suffix = ".webm"
    elif "mp4" in content_type or suffix in {".mp4", ".m4a"}:
        suffix = ".m4a"
    elif suffix not in {".mp3", ".m4a", ".wav"}:
        _backend_log(f"upload rejected: unsupported suffix={suffix}", level="ERROR")
        raise HTTPException(415, "Use MP4/M4A, WebM, WAV, or MP3 audio")

    job_id = uuid.uuid4().hex
    _backend_log(f"upload read: {len(audio)} bytes filename={filename} suffix={suffix}", job_id=job_id)
    job_dir = DATA / job_id
    job_dir.mkdir(parents=True)
    raw_path = job_dir / f"source{suffix}"
    raw_path.write_bytes(audio)
    _backend_log("normalizing input with ffmpeg", job_id=job_id)
    try:
        source_path = _to_wav(raw_path)
    except HTTPException as exc:
        _backend_log(f"ffmpeg normalization failed: {exc.detail}", job_id=job_id, level="ERROR")
        raise
    _backend_log(f"input ready: {source_path.name} bytes={source_path.stat().st_size}", job_id=job_id)
    JOBS[job_id] = {
        "id": job_id,
        "status": "queued",
        "message": "正在准备音乐模型生成…",
        "provider": _make_provider().status(),
        "plan": "all",
        "plan_name": "Funk 和 Lo-fi",
        "control_profile": control_profile,
        "cfg_scale": cfg_scale,
        "steps": steps,
        "seed": seed,
        "plans": [
            {"id": plan_id, "name": plan["name"], "description": plan["description"], "noise": plan["noise"]}
            for plan_id, plan in PROMPT_PLANS.items()
        ],
        "prompts": {
            plan_id: plan["prompt"] for plan_id, plan in PROMPT_PLANS.items()
        },
        "prompt_translations": {plan_id: plan["translation"] for plan_id, plan in PROMPT_PLANS.items()},
        "noise_levels": {plan_id: plan["noise"] for plan_id, plan in PROMPT_PLANS.items()},
        "variants": {},
        "error": None,
        "source_path": str(source_path),
    }
    (job_dir / "prompt_snapshot.json").write_text(json.dumps({
        "plan": "all",
        "plans": JOBS[job_id]["plans"],
        "prompts": JOBS[job_id]["prompts"],
        "translations": JOBS[job_id]["prompt_translations"],
        "seed": StableAudioClient().config.seed,
        "noise": StableAudioClient().config.init_noise_level,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    asyncio.create_task(_run_generation(job_id, source_path, job_dir))
    _backend_log("job queued; returning 202 for Funk and Lo-fi plans", job_id=job_id)
    return JSONResponse({"id": job_id, "status": "queued"}, status_code=202)


@app.get("/api/generations/{job_id}")
async def get_generation(job_id: str) -> dict[str, Any]:
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Generation not found")
    result = {key: value for key, value in job.items() if key != "source_path"}
    for variant_id, variant in result.get("variants", {}).items():
        variant["audio_url"] = f"/api/generations/{job_id}/audio/{variant_id}"
    return result


@app.get("/api/generations/{job_id}/audio/{variant_id}")
async def get_audio(job_id: str, variant_id: str) -> FileResponse:
    if not re.fullmatch(r"[0-9a-f]{32}", job_id):
        raise HTTPException(404, "Generation not found")
    job = JOBS.get(job_id)
    variant = job.get("variants", {}).get(variant_id) if job else None
    if not variant:
        raise HTTPException(404, "Variant not found")
    path = DATA / job_id / f"{variant_id}.wav"
    if not path.is_file():
        raise HTTPException(404, "Audio is not ready")
    return FileResponse(
        path,
        media_type="audio/wav",
        filename=path.name,
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@app.get("/api/generations/{job_id}/source")
async def get_source(job_id: str) -> FileResponse:
    if not re.fullmatch(r"[0-9a-f]{32}", job_id):
        raise HTTPException(404, "Generation not found")
    job = JOBS.get(job_id)
    path = Path(job.get("source_path", "")) if job else None
    if not path or not path.is_file():
        raise HTTPException(404, "Source audio is not ready")
    return FileResponse(path, media_type="audio/wav", filename="source.wav")


async def _run_generation(job_id: str, audio_path: Path, job_dir: Path) -> None:
    job = JOBS[job_id]
    provider = _make_provider()
    job["provider"] = provider.status()
    _backend_log(
        f"worker started provider={provider.status()} plans={len(PROMPT_PLANS)}",
        job_id=job_id,
    )
    try:
        if isinstance(provider, StableAudioClient) and not provider.script.is_file():
            _backend_log(f"Stable Audio CLI missing: {provider.script}", job_id=job_id, level="ERROR")
            raise StableAudioError(
                f"Stable Audio CLI not found: {provider.script}. "
                "Set STABLE_AUDIO_ROOT to optimized/tflite."
            )
        input_seconds = StableAudioClient.audio_seconds(audio_path)
        if input_seconds is None:
            input_seconds = getattr(getattr(provider, "config", None), "output_seconds", 10.0)
            _backend_log(
                f"could not read input duration; using configured fallback {input_seconds}s",
                job_id=job_id,
                level="WARN",
            )
        else:
            input_seconds = round(max(1.0, input_seconds), 3)
            _backend_log(f"generation duration locked to input: {input_seconds}s", job_id=job_id)
        job["input_seconds"] = input_seconds
        job["status"] = "generating"
        total_variants = len(PROMPT_PLANS)
        completed_variants = 0
        for plan_id, plan in PROMPT_PLANS.items():
            variant_id = plan_id
            job["message"] = f"音乐模型正在生成方案 {plan['name']}（{completed_variants}/{total_variants}）…"
            _backend_log(
                f"generation started plan={plan_id} noise={plan['noise']}",
                job_id=job_id,
            )
            logger.info(
                "audio_provider_start job=%s plan=%s noise=%s provider=%s",
                job_id, plan_id, plan["noise"], provider.status(),
            )
            output_path = job_dir / f"{variant_id}.wav"
            try:
                render_kwargs = {
                    "output_seconds": input_seconds,
                    "prompt": plan["prompt"],
                    "init_noise_level": plan["noise"],
                }
                if isinstance(provider, DiffSynthRemoteClient):
                    profile = job.get("control_profile", "prosody")
                    render_kwargs.update({
                        "control_profile": profile,
                        "denoising_strength": 0.25 if profile == "anchored_low" else 0.45 if profile == "anchored_medium" else None,
                        "cfg_scale": float(job.get("cfg_scale", "4")),
                        "steps": int(job.get("steps", "50")),
                        "seed": int(job.get("seed", "101")),
                    })
                diagnostics = await asyncio.to_thread(provider.render, audio_path, "transform", output_path, **render_kwargs)
            except Exception as exc:
                _backend_log(
                    f"generation failed plan={plan_id}: {_friendly_error(exc)}",
                    job_id=job_id,
                    level="ERROR",
                )
                logger.exception("audio_provider_failed job=%s plan=%s", job_id, plan_id)
                job["variants"][variant_id] = {
                    "plan": plan_id,
                    "plan_name": plan["name"],
                    "status": "failed",
                    "error": _friendly_error(exc),
                }
                continue
            job["variants"][variant_id] = {
                "plan": plan_id,
                "plan_name": plan["name"],
                "status": "completed",
                "prompt": plan["prompt"],
                "prompt_zh": plan["translation"],
                "noise": plan["noise"],
                **diagnostics,
            }
            completed_variants += 1
            _backend_log(
                f"generation completed plan={plan_id} noise={plan['noise']} seconds={diagnostics.get('seconds')} bytes={diagnostics.get('bytes')}",
                job_id=job_id,
            )
            logger.info(
                "audio_provider_complete job=%s plan=%s diagnostics=%s",
                job_id, plan_id, diagnostics,
            )
        if all(item.get("status") == "completed" for item in job["variants"].values()) and len(job["variants"]) == total_variants:
            job["status"] = "completed"
            job["message"] = "五种纵向方案音频已生成"
            _backend_log("job completed: all five vertical plans ready", job_id=job_id)
        else:
            job["status"] = "failed"
            job["message"] = "纵向方案生成失败"
            job["error"] = "; ".join(
                item.get("error", "unknown")
                for item in job["variants"].values()
                if item.get("status") == "failed"
            ) or "no variant completed"
            _backend_log(f"job failed: {job['error']}", job_id=job_id, level="ERROR")
    except Exception as exc:
        logger.exception("audio_provider_generation_failed job=%s", job_id)
        job["status"] = "failed"
        job["error"] = _friendly_error(exc)
        job["message"] = "音乐模型生成失败"
        _backend_log(f"worker crashed: {job['error']}", job_id=job_id, level="ERROR")


def _to_wav(source: Path) -> Path:
    target = source.parent / "audio_44k_stereo.wav"
    try:
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        subprocess.run(
            [
                ffmpeg, "-y", "-i", str(source), "-vn",
                "-af", "loudnorm=I=-14:TP=-1.0:LRA=7",
                "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le", str(target),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=30,
        )
    except Exception as exc:
        raise HTTPException(415, "Could not convert browser recording to WAV") from exc
    return target


def _friendly_error(exc: Exception) -> str:
    if isinstance(exc, (StableAudioError, DiffSynthRemoteError, HTTPException)):
        return str(exc.detail) if isinstance(exc, HTTPException) else str(exc)
    return f"{type(exc).__name__}: {exc}"


def _make_provider() -> StableAudioClient | DiffSynthRemoteClient:
    mode = os.getenv("H2M_AUDIO_PROVIDER", "stable_audio").strip().lower()
    if mode in {"diffsynth", "diffsynth_remote", "diffsynth-music"}:
        return DiffSynthRemoteClient()
    if mode in {"stable_audio", "stable-audio", "stable_audio_local"}:
        return StableAudioClient()
    raise RuntimeError(
        f"Unknown H2M_AUDIO_PROVIDER={mode!r}; use stable_audio or diffsynth_remote"
    )
