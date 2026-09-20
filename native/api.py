"""Clean FastAPI backend for the native DiffSynth-Music demo.

This module is deliberately independent from the historical ``app`` package.
It owns files and task state; the GPU Worker owns all model execution.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import subprocess
import time
import urllib.parse
import uuid
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .prompts import STYLE_ORDER, style_snapshot
from .worker_client import NativeWorkerClient, WorkerError


logger = logging.getLogger("hum2midi.native")
ROOT = Path(__file__).resolve().parent
DATA = Path(os.getenv("NATIVE_DATA_DIR", "runtime/native_jobs"))
if not DATA.is_absolute():
    DATA = ROOT.parent / DATA
DATA.mkdir(parents=True, exist_ok=True)
STATIC = ROOT / "static"
MAX_UPLOAD = max(1, int(os.getenv("NATIVE_MAX_UPLOAD_MB", "20"))) * 1024 * 1024
SEED = int(os.getenv("NATIVE_SEED", "42"))
CFG_SCALE = float(os.getenv("NATIVE_CFG_SCALE", "4"))
STEPS = max(1, int(os.getenv("NATIVE_STEPS", "50")))

app = FastAPI(title="Hum2Midi Native Demo")
app.mount("/static", StaticFiles(directory=STATIC), name="static")
JOBS: dict[str, dict[str, Any]] = {}
TASKS: dict[str, asyncio.Task[None]] = {}
GENERATION_LOCK = asyncio.Lock()
LOGS: list[dict[str, Any]] = []
LOG_SEQ = 0


def _log(message: str, *, job_id: str | None = None, level: str = "INFO") -> None:
    global LOG_SEQ
    LOG_SEQ += 1
    entry = {
        "seq": LOG_SEQ,
        "time": datetime.now().strftime("%H:%M:%S"),
        "level": level,
        "message": message,
    }
    if job_id:
        entry["job_id"] = job_id
    LOGS.append(entry)
    del LOGS[:-500]
    logger.info("native %s", entry)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _manifest_path(job_id: str) -> Path:
    return DATA / job_id / "request.json"


def _save_job(job: dict[str, Any]) -> None:
    path = _manifest_path(str(job["id"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")


def _job_public(job: dict[str, Any]) -> dict[str, Any]:
    result = json.loads(json.dumps(job))
    result.pop("source_path", None)
    for variant_id, variant in result.get("variants", {}).items():
        if variant.get("status") == "completed":
            variant["audio_url"] = f"/api/generations/{job['id']}/audio/{variant_id}"
    result["source_url"] = f"/api/generations/{job['id']}/source"
    return result


@app.get("/", response_class=HTMLResponse)
async def home() -> FileResponse:
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/api/health")
async def health() -> dict[str, Any]:
    worker = NativeWorkerClient()
    remote = worker.health()
    status = "ok" if remote.get("status") == "ok" else "degraded"
    return {
        "status": status,
        "service": "hum2midi-native",
        "provider": "DiffSynth-Music Prosody",
        "worker": remote,
        "styles": list(STYLE_ORDER),
        "generation_lock": GENERATION_LOCK.locked(),
    }


@app.get("/api/debug/logs")
async def debug_logs(since: int = 0, limit: int = 200) -> dict[str, Any]:
    limit = max(1, min(limit, 500))
    return {"cursor": LOG_SEQ, "logs": [item for item in LOGS if item["seq"] > since][-limit:]}


@app.get("/api/prompt-presets")
async def prompt_presets() -> dict[str, Any]:
    styles = style_snapshot()
    return {
        "styles": [
            {"id": key, **value, "negative_prompt_source": "worker.pipe.default_negative_prompt"}
            for key, value in styles.items()
        ],
        "parameters": {"seed": SEED, "cfg_scale": CFG_SCALE, "steps": STEPS, "control": "prosody"},
    }


@app.post("/api/generations", status_code=202)
async def create_generation(request: Request) -> JSONResponse:
    body = await request.body()
    if not body:
        raise HTTPException(400, "No audio data received")
    if len(body) > MAX_UPLOAD:
        raise HTTPException(413, f"Audio exceeds {MAX_UPLOAD // (1024 * 1024)} MB")
    encoded_filename = request.headers.get("x-audio-filename", "hum.m4a")
    filename = urllib.parse.unquote(encoded_filename) or "hum.m4a"
    suffix = Path(filename).suffix.lower()
    if suffix not in {".m4a", ".mp4", ".wav", ".mp3", ".webm", ".ogg"}:
        raise HTTPException(415, "Use M4A, MP4, WAV, MP3, WebM, or OGG audio")
    job_id = uuid.uuid4().hex
    job_dir = DATA / job_id
    job_dir.mkdir(parents=True, exist_ok=False)
    source = job_dir / f"source{suffix}"
    source.write_bytes(body)
    normalized = _normalize(source, job_dir)
    input_seconds = _audio_seconds(normalized)
    if input_seconds is None or input_seconds <= 0:
        raise HTTPException(415, "Could not read the uploaded audio duration")
    prompt_data = style_snapshot()
    job: dict[str, Any] = {
        "id": job_id,
        "status": "queued",
        "message": "已接收录音，等待 Prosody Worker…",
        "created_at": _now(),
        "source_path": str(normalized),
        "source_name": filename,
        "source_sha256": hashlib.sha256(body).hexdigest(),
        "input_seconds": input_seconds,
        "provider": "DiffSynth-Music Prosody",
        "parameters": {"seed": SEED, "cfg_scale": CFG_SCALE, "steps": STEPS, "control": "prosody"},
        "styles": prompt_data,
        "variants": {
            style: {
                "id": style,
                "name": prompt_data[style]["name"],
                "status": "queued",
                "prompt": prompt_data[style]["prompt"],
                "translation": prompt_data[style]["translation"],
            }
            for style in STYLE_ORDER
        },
    }
    JOBS[job_id] = job
    _save_job(job)
    _log(f"job queued input_seconds={input_seconds:.3f}s styles={','.join(STYLE_ORDER)}", job_id=job_id)
    TASKS[job_id] = asyncio.create_task(_run_generation(job_id))
    return JSONResponse({"id": job_id, "status": "queued"}, status_code=202)


@app.get("/api/generations/{job_id}")
async def get_generation(job_id: str) -> dict[str, Any]:
    job = JOBS.get(job_id) or _load_job(job_id)
    if not job:
        raise HTTPException(404, "Generation not found")
    return _job_public(job)


@app.get("/api/generations/{job_id}/source")
async def get_source(job_id: str) -> FileResponse:
    job = JOBS.get(job_id) or _load_job(job_id)
    path = Path(job.get("source_path", "")) if job else None
    if not path or not path.is_file() or not path.is_relative_to(DATA):
        raise HTTPException(404, "Source audio is not ready")
    return FileResponse(path, media_type="audio/wav", filename="input-normalized.wav")


@app.get("/api/generations/{job_id}/audio/{variant_id}")
async def get_audio(job_id: str, variant_id: str) -> FileResponse:
    job = JOBS.get(job_id) or _load_job(job_id)
    if not job or variant_id not in STYLE_ORDER:
        raise HTTPException(404, "Generation not found")
    variant = job.get("variants", {}).get(variant_id, {})
    path = DATA / job_id / f"{variant_id}.wav"
    if variant.get("status") != "completed" or not path.is_file():
        raise HTTPException(404, "Audio is not ready")
    return FileResponse(path, media_type="audio/wav", filename=f"{variant_id}.wav")


async def _run_generation(job_id: str) -> None:
    job = JOBS[job_id]
    async with GENERATION_LOCK:
        job["status"] = "running"
        job["started_at"] = _now()
        job["message"] = "模型正在生成第一个风格…"
        _save_job(job)
        _log("generation lock acquired", job_id=job_id)
        worker = NativeWorkerClient()
        source = Path(job["source_path"])
        try:
            for index, style in enumerate(STYLE_ORDER):
                variant = job["variants"][style]
                variant["status"] = "running"
                variant["started_at"] = _now()
                job["message"] = f"正在生成 {variant['name']}（{index + 1}/{len(STYLE_ORDER)}）…"
                _save_job(job)
                _log(f"variant start style={style}", job_id=job_id)
                started = time.perf_counter()
                try:
                    diagnostics = await asyncio.to_thread(
                        worker.generate,
                        source,
                        DATA / job_id / f"{style}.wav",
                        prompt=variant["prompt"],
                        seed=SEED,
                        cfg_scale=CFG_SCALE,
                        steps=STEPS,
                    )
                    variant.update(
                        {
                            "status": "completed",
                            "finished_at": _now(),
                            "elapsed_seconds": round(time.perf_counter() - started, 3),
                            "diagnostics": diagnostics,
                        }
                    )
                    _log(f"variant completed style={style} elapsed={variant['elapsed_seconds']}s", job_id=job_id)
                except Exception as exc:
                    variant.update(
                        {
                            "status": "failed",
                            "finished_at": _now(),
                            "elapsed_seconds": round(time.perf_counter() - started, 3),
                            "error": str(exc),
                        }
                    )
                    _log(f"variant failed style={style}: {exc}", job_id=job_id, level="ERROR")
                _save_job(job)
            completed = [item for item in job["variants"].values() if item["status"] == "completed"]
            if len(completed) == len(STYLE_ORDER):
                job["status"] = "completed"
                job["message"] = "Funk 和 Lo-fi 都已生成。"
            elif completed:
                job["status"] = "partial"
                job["message"] = "已有一个风格生成完成，另一个生成失败。"
            else:
                job["status"] = "failed"
                job["message"] = "两个风格都生成失败。"
                job["error"] = "；".join(item.get("error", "unknown") for item in job["variants"].values())
        finally:
            job["finished_at"] = _now()
            _save_job(job)
            _log(f"generation finished status={job.get('status')}", job_id=job_id)
            TASKS.pop(job_id, None)


def _load_job(job_id: str) -> dict[str, Any] | None:
    path = _manifest_path(job_id)
    if not path.is_file() or not path.is_relative_to(DATA):
        return None
    try:
        job = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(job, dict):
        JOBS[job_id] = job
        return job
    return None


def _normalize(source: Path, job_dir: Path) -> Path:
    target = job_dir / "input-normalized.wav"
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        try:
            import imageio_ffmpeg

            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            ffmpeg = None
    if not ffmpeg:
        if source.suffix.lower() == ".wav":
            return source
        raise HTTPException(503, "ffmpeg is required to normalize this audio format")
    try:
        subprocess.run(
            [ffmpeg, "-y", "-i", str(source), "-vn", "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le", str(target)],
            check=True,
            capture_output=True,
            timeout=45,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(415, "Could not normalize uploaded audio") from exc
    return target


def _audio_seconds(path: Path) -> float | None:
    try:
        with wave.open(str(path), "rb") as handle:
            return round(handle.getnframes() / handle.getframerate(), 3)
    except (OSError, wave.Error, ZeroDivisionError):
        return None
