"""Pluggable cloud transcription engines.

The application keeps a deterministic DSP path for reproducibility, while a
specialized cloud vocal transcriber can be enabled for higher-fidelity MIDI.
This module owns the provider protocol so changing providers does not spread
HTTP details through the generation pipeline.
"""
from __future__ import annotations

import asyncio
import io
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx
import mido

from app.pitch_tracking import _classify_contour


class CloudTranscriptionError(RuntimeError):
    pass


class TranscriptionEngine(Protocol):
    """Small provider boundary for the reviewable transcription pipeline."""

    name: str

    async def transcribe(self, audio_path: Path) -> dict[str, Any]:
        ...


@dataclass(frozen=True)
class DspTranscriptionEngine:
    """Deterministic baseline used for local measurements and regression tests."""

    name: str = "dsp-yin"

    async def transcribe(self, audio_path: Path) -> dict[str, Any]:
        # Import lazily so the provider boundary remains independent of the
        # application module and cloud adapters.
        from app.pitch_tracking import extract_hummed_notes

        return extract_hummed_notes(audio_path)


@dataclass(frozen=True)
class KlangioTranscriptionEngine:
    """Cloud vocal transcription provider, selected explicitly at runtime."""

    name: str = "klangio-vocal-cloud"

    async def transcribe(self, audio_path: Path) -> dict[str, Any]:
        return await transcribe_with_klangio(audio_path)


def resolve_transcription_engine() -> TranscriptionEngine:
    """Resolve one engine without silently falling back after a cloud failure.

    ``auto`` only chooses Klangio when a key is present; once selected, a
    provider error remains visible to the caller. This prevents an A/B run
    from looking successful because it quietly switched engines.
    """
    configured = os.getenv("TRANSCRIPTION_ENGINE", "dsp").strip().lower()
    if configured in {"klangio", "auto"} and os.getenv("KLANGIO_API_KEY", "").strip():
        return KlangioTranscriptionEngine()
    if configured == "klangio":
        raise CloudTranscriptionError("已选择 Klangio Vocal 转谱，但服务端未配置 KLANGIO_API_KEY。")
    if configured not in {"dsp", "auto"}:
        raise CloudTranscriptionError(f"未知 TRANSCRIPTION_ENGINE：{configured}")
    return DspTranscriptionEngine()


async def transcribe_with_klangio(audio_path: Path) -> dict[str, Any]:
    """Transcribe a monophonic vocal take through Klangio's vocal model."""
    api_key = os.getenv("KLANGIO_API_KEY", "").strip()
    if not api_key:
        raise CloudTranscriptionError("未配置 KLANGIO_API_KEY，无法使用云端 Vocal 转谱。")
    base_url = os.getenv("KLANGIO_BASE_URL", "https://api.klang.io").rstrip("/")
    model = os.getenv("KLANGIO_MODEL", "vocal").strip() or "vocal"
    timeout_seconds = float(os.getenv("KLANGIO_TIMEOUT_SECONDS", "120"))
    headers = {"kl-api-key": api_key}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_seconds, connect=20)) as client:
            with audio_path.open("rb") as handle:
                response = await client.post(
                    f"{base_url}/transcription",
                    headers=headers,
                    params={"model": model},
                    data=[("outputs", "midi")],
                    files={"file": (audio_path.name, handle, "audio/wav")},
                )
            if response.is_error:
                raise CloudTranscriptionError(
                    f"Klangio API {response.status_code}: {response.text[:500]}"
                )
            created = response.json()
            job_id = created.get("job_id")
            if not job_id:
                raise CloudTranscriptionError("Klangio 没有返回 job_id。")

            interval = float(os.getenv("KLANGIO_POLL_SECONDS", "2"))
            deadline = asyncio.get_running_loop().time() + timeout_seconds
            while True:
                if asyncio.get_running_loop().time() >= deadline:
                    raise CloudTranscriptionError("Klangio 转谱等待超时。")
                await asyncio.sleep(interval)
                status_response = await client.get(
                    f"{base_url}/job/{job_id}/status", headers=headers
                )
                if status_response.is_error:
                    raise CloudTranscriptionError(
                        f"Klangio 状态查询 {status_response.status_code}: {status_response.text[:500]}"
                    )
                status = status_response.json().get("status", "").upper()
                if status == "FAILED":
                    raise CloudTranscriptionError(
                        f"Klangio 转谱失败：{status_response.text[:500]}"
                    )
                if status == "COMPLETED":
                    break

            midi_response = await client.get(f"{base_url}/job/{job_id}/midi", headers=headers)
            if midi_response.is_error:
                raise CloudTranscriptionError(
                    f"Klangio MIDI 下载 {midi_response.status_code}: {midi_response.text[:500]}"
                )
            return _midi_to_analysis(midi_response.content)
    except httpx.HTTPError as exc:
        raise CloudTranscriptionError(f"连接 Klangio 失败：{exc}") from exc


def _midi_to_analysis(data: bytes) -> dict[str, Any]:
    """Convert a provider MIDI result into the app's MelodyIR input shape."""
    try:
        midi = mido.MidiFile(file=io.BytesIO(data))
    except Exception as exc:
        raise CloudTranscriptionError("Klangio 返回的文件不是有效 MIDI。") from exc

    tempo = 500_000
    ticks = 0
    active: dict[tuple[int, int], list[tuple[int, int]]] = {}
    notes: list[dict[str, Any]] = []
    for message in midi.merged_track:
        ticks += message.time
        if message.type == "set_tempo":
            tempo = message.tempo
        elif message.type == "note_on" and message.velocity > 0:
            active.setdefault((message.channel, message.note), []).append((ticks, message.velocity))
        elif message.type == "note_off" or (message.type == "note_on" and message.velocity == 0):
            key = (message.channel, message.note)
            starts = active.get(key)
            if not starts:
                continue
            start_tick, velocity = starts.pop(0)
            start = mido.tick2second(start_tick, midi.ticks_per_beat, tempo)
            end = mido.tick2second(ticks, midi.ticks_per_beat, tempo)
            duration = end - start
            if 36 <= key[1] <= 84 and duration >= 0.08:
                notes.append({
                    "pitch": key[1],
                    "pitch_cents": 0.0,
                    "start": round(start, 3),
                    "duration": round(duration, 3),
                    "velocity": max(1, min(127, int(velocity))),
                    "confidence": 0.85,
                    "quantization_margin_cents": 50.0,
                })
    notes.sort(key=lambda item: item["start"])
    if len(notes) < 2:
        raise CloudTranscriptionError("Klangio 没有返回足够的单声部音符。")
    boundaries = [
        current["start"]
        for previous, current in zip(notes, notes[1:])
        if current["start"] - (previous["start"] + previous["duration"]) >= 0.28
    ]
    bpm = 60_000_000 / tempo
    return {
        "tempo_bpm": round(float(max(50, min(200, bpm))), 2),
        "notes": notes,
        "pitch_trace": [],
        "phrase_boundaries": boundaries,
        "pitch_contour": _classify_contour([note["pitch"] for note in notes]),
        "source": "klangio-vocal-cloud",
    }
