from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import uuid
from pathlib import Path
from typing import Any

import imageio_ffmpeg
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.audio_understanding import AudioUnderstandingError, QwenOmniClient
from app.ir import melody_to_midi
from app.midi_style import MidiStyleError, QwenMidiStyleClient, arrangement_to_midi
from app.pitch_tracking import PitchTrackingError, extract_hummed_notes
from app.audio_renderer import render_audio, renderer_status

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT.parent / ".env")
STATIC = ROOT / "static"
DATA = Path(os.getenv("H2M_DATA_DIR", "data/demo"))
DATA.mkdir(parents=True, exist_ok=True)
MAX_UPLOAD = int(os.getenv("MAX_UPLOAD_MB", "10")) * 1024 * 1024
JOBS: dict[str, dict[str, Any]] = {}
app = FastAPI(title="Make Anything Musical Demo")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/", response_class=HTMLResponse)
async def home() -> FileResponse:
    return FileResponse(
        STATIC / "index.html",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {
        "status": "ok",
        "audio_understanding": "qwen-omni-cloud",
        "renderer": renderer_status(),
        "midi_style_model": os.getenv("QWEN_TEXT_MODEL", "qwen-flash"),
    }


@app.post("/api/generations", status_code=202)
async def create_generation(request: Request) -> JSONResponse:
    audio = await request.body()
    if not audio:
        raise HTTPException(400, "No audio data received")
    if len(audio) > MAX_UPLOAD:
        raise HTTPException(413, f"Audio exceeds {MAX_UPLOAD // (1024 * 1024)} MB")

    filename = request.headers.get("x-audio-filename", "hum.m4a")
    suffix = Path(filename).suffix.lower()
    content_type = request.headers.get("content-type", "").lower()
    if "webm" in content_type or suffix == ".webm":
        suffix = ".webm"
    elif "mp4" in content_type or suffix in {".mp4", ".m4a"}:
        suffix = ".m4a"
    elif suffix not in {".mp3", ".m4a", ".wav"}:
        raise HTTPException(415, "Use a browser that records MP4/M4A, WebM, WAV, or MP3 audio")

    job_id = uuid.uuid4().hex
    job_dir = DATA / job_id
    job_dir.mkdir(parents=True)
    raw_path = job_dir / f"source{suffix}"
    raw_path.write_bytes(audio)
    source_path = _to_wav(raw_path)
    JOBS[job_id] = {
        "id": job_id,
        "status": "queued",
        "message": "正在请云端核对音高走向，并检测音符和节奏…",
        "variants": {},
        "error": None,
        "ir": None,
        "pitch_trace": [],
        "renderers": {},
    }
    asyncio.create_task(_run_generation(job_id, source_path, job_dir))
    return JSONResponse({"id": job_id, "status": "queued"}, status_code=202)


@app.get("/api/generations/{job_id}")
async def get_generation(job_id: str) -> dict[str, Any]:
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Generation not found")
    result = {key: value for key, value in job.items() if key not in {"ir", "pitch_trace"}}
    if job.get("ir"):
        ir = job["ir"]
        result["melody"] = {
            "tempo_bpm": ir["tempo_bpm"],
            "phrase_boundaries": ir.get("phrase_boundaries", []),
            "note_events": [
                {key: note[key] for key in (
                    "pitch", "start", "duration", "quantization_margin_cents",
                ) if key in note}
                for note in ir.get("note_events", [])
            ],
            "pitch_trace": job.get("pitch_trace", []),
        }
    for variant in result.get("variants", {}).values():
        variant["audio_url"] = f"/api/generations/{job_id}/audio/{variant['style']}"
    return result


@app.get("/api/generations/{job_id}/audio/{style}")
async def get_audio(job_id: str, style: str) -> FileResponse:
    if not re.fullmatch(r"[0-9a-f]{32}", job_id):
        raise HTTPException(404, "Generation not found")
    if style not in {"funk", "lofi"}:
        raise HTTPException(404, "Variant not found")
    path = DATA / job_id / f"{style}.wav"
    if not path.is_file():
        raise HTTPException(404, "Audio is not ready")
    return FileResponse(
        path,
        media_type="audio/wav",
        filename=path.name,
        headers={"Cache-Control": "no-store, max-age=0"},
    )


async def _run_generation(job_id: str, audio_path: Path, job_dir: Path) -> None:
    job = JOBS[job_id]
    try:
        audio_client = QwenOmniClient()
        style_client = QwenMidiStyleClient()
        job["status"] = "understanding"
        job["message"] = "云端正在理解哼唱，并准备旋律 MIDI…"
        contour = await audio_client.analyze_pitch_contour(audio_path)
        analysis = extract_hummed_notes(audio_path)
        if analysis["pitch_contour"] != contour:
            raise AudioUnderstandingError("云端听到的旋律走向与录音分析不一致，请重录后重试。")
        midi_data, ir = melody_to_midi(analysis)
        job["ir"] = ir
        job["pitch_trace"] = analysis.get("pitch_trace", [])
        (job_dir / "melody.mid").write_bytes(midi_data)
        (job_dir / "melody_ir.json").write_text(json.dumps(ir, ensure_ascii=False, indent=2), encoding="utf-8")
        job["status"] = "styling"
        job["message"] = "文本模型正在根据原始 MIDI 整体生成 Funk 与 Lofi 风格…"

        async def style_and_render(style: str) -> bytes | Exception:
            try:
                plan = await style_client.expand(ir, style, contour)
                styled_midi = arrangement_to_midi(plan, style=style)
                (job_dir / f"{style}.mid").write_bytes(styled_midi)
                (job_dir / f"{style}_plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
                backend = await asyncio.to_thread(
                    render_audio, job_dir / f"{style}.mid", style, job_dir / f"{style}.wav"
                )
                job.setdefault("renderers", {})[style] = backend
                logger.info("generation_style_complete job=%s style=%s", job_id, style)
                return styled_midi
            except Exception as exc:
                logger.exception("generation_style_failed job=%s style=%s error_type=%s", job_id, style, type(exc).__name__)
                return exc

        # Run the two larger model generations sequentially. Concurrent
        # requests can contend for the workspace queue and make one style
        # wait until the HTTP timeout even though a small request is fast.
        outcomes = []
        for style in ("funk", "lofi"):
            outcomes.append(await style_and_render(style))
        for style, outcome in zip(("funk", "lofi"), outcomes):
            if isinstance(outcome, Exception):
                job["variants"][style] = {"style": style, "status": "failed", "error": str(outcome)}
            else:
                job["variants"][style] = {"style": style, "status": "completed"}
        if all(item.get("status") == "completed" for item in job["variants"].values()):
            job["status"] = "completed"
            job["message"] = "模型风格 MIDI 和 Funk/Lofi 音频已生成"
        else:
            job["status"] = "failed"
            job["message"] = "风格 MIDI 或音频生成失败"
            job["error"] = "; ".join(
                item.get("error", "unknown")
                for item in job["variants"].values()
                if item.get("status") == "failed"
            )
    except Exception as exc:
        job["status"] = "failed"
        job["error"] = _friendly_error(exc)
        job["message"] = "生成失败"

def _to_wav(source: Path) -> Path:
    target = source.parent / "audio_16k.wav"
    try:
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        subprocess.run(
            [ffmpeg, "-y", "-i", str(source), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(target)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=30,
        )
    except Exception as exc:
        raise HTTPException(415, "Could not convert browser recording to WAV") from exc
    return target


def _friendly_error(exc: Exception) -> str:
    if isinstance(exc, AudioUnderstandingError):
        return str(exc)
    if isinstance(exc, PitchTrackingError):
        return str(exc)
    if isinstance(exc, MidiStyleError):
        return str(exc)
    if isinstance(exc, HTTPException):
        return str(exc.detail)
    return f"{type(exc).__name__}: {exc}"
