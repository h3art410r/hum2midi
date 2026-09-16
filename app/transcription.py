"""Pluggable cloud transcription engines.

The application keeps a deterministic DSP path for reproducibility, while a
specialized cloud vocal transcriber can be enabled for higher-fidelity MIDI.
This module owns the provider protocol so changing providers does not spread
HTTP details through the generation pipeline.
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import uuid
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

    async def transcribe(self, audio_path: Path, *, public_url: str | None = None) -> dict[str, Any]:
        ...


@dataclass(frozen=True)
class DspTranscriptionEngine:
    """Deterministic baseline used for local measurements and regression tests."""

    name: str = "dsp-yin"

    async def transcribe(self, audio_path: Path, *, public_url: str | None = None) -> dict[str, Any]:
        # Import lazily so the provider boundary remains independent of the
        # application module and cloud adapters.
        from app.pitch_tracking import extract_hummed_notes

        return extract_hummed_notes(audio_path)


@dataclass(frozen=True)
class KlangioTranscriptionEngine:
    """Cloud vocal transcription provider, selected explicitly at runtime."""

    name: str = "klangio-vocal-cloud"

    async def transcribe(self, audio_path: Path, *, public_url: str | None = None) -> dict[str, Any]:
        return await transcribe_with_klangio(audio_path)


@dataclass(frozen=True)
class TencentTranscriptionEngine:
    """Tencent Media Lab's mainland vocal-to-MIDI cloud provider."""

    name: str = "tencent-vocal-cloud"

    async def transcribe(self, audio_path: Path, *, public_url: str | None = None) -> dict[str, Any]:
        return await transcribe_with_tencent(audio_path, public_url=public_url)


def resolve_transcription_engine() -> TranscriptionEngine:
    """Resolve one engine without silently falling back after a cloud failure.

    ``auto`` only chooses Klangio when a key is present; once selected, a
    provider error remains visible to the caller. This prevents an A/B run
    from looking successful because it quietly switched engines.
    """
    configured = os.getenv("TRANSCRIPTION_ENGINE", "dsp").strip().lower()
    if configured in {"tencent", "auto"} and os.getenv("TENCENT_SECRET_ID", "").strip() and os.getenv("TENCENT_SECRET_KEY", "").strip():
        return TencentTranscriptionEngine()
    if configured == "tencent":
        raise CloudTranscriptionError("已选择腾讯云人声转录，但服务端未配置 TENCENT_SECRET_ID/TENCENT_SECRET_KEY。")
    if configured in {"klangio", "auto"} and os.getenv("KLANGIO_API_KEY", "").strip():
        return KlangioTranscriptionEngine()
    if configured == "klangio":
        raise CloudTranscriptionError("已选择 Klangio Vocal 转谱，但服务端未配置 KLANGIO_API_KEY。")
    if configured not in {"dsp", "auto"}:
        raise CloudTranscriptionError(f"未知 TRANSCRIPTION_ENGINE：{configured}")
    return DspTranscriptionEngine()


def configured_transcription_engine_name() -> str:
    """Return the effective engine name for health/debug output."""
    configured = os.getenv("TRANSCRIPTION_ENGINE", "dsp").strip().lower()
    if configured in {"tencent", "auto"} and os.getenv("TENCENT_SECRET_ID", "").strip() and os.getenv("TENCENT_SECRET_KEY", "").strip():
        return TencentTranscriptionEngine.name
    if configured in {"klangio", "auto"} and os.getenv("KLANGIO_API_KEY", "").strip():
        return KlangioTranscriptionEngine.name
    if configured in {"dsp", "auto"}:
        return DspTranscriptionEngine.name
    return f"invalid:{configured or 'empty'}"


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


async def transcribe_with_tencent(audio_path: Path, *, public_url: str | None = None) -> dict[str, Any]:
    """Run Tencent's asynchronous vocalMidi task and fetch its MIDI/JSON output."""
    secret_id = os.getenv("TENCENT_SECRET_ID", "").strip()
    secret_key = os.getenv("TENCENT_SECRET_KEY", "").strip()
    if not secret_id or not secret_key:
        raise CloudTranscriptionError("未配置 TENCENT_SECRET_ID/TENCENT_SECRET_KEY，无法使用腾讯云人声转录。")
    if not public_url:
        raise CloudTranscriptionError("腾讯云人声转录需要 H2M_PUBLIC_BASE_URL，以便读取临时音频。")
    endpoint = os.getenv("TENCENT_SMART_MUSIC_URL", "https://api.mediax.tencent.com/job").strip()
    timeout_seconds = float(os.getenv("TENCENT_TIMEOUT_SECONDS", "120"))
    custom_id = f"hum2midi-{uuid.uuid4().hex}"
    create_payload = {
        "action": "CreateJob",
        "secretId": secret_id,
        "secretKey": secret_key,
        "createJobRequest": {
            "customId": custom_id,
            "timeout": int(timeout_seconds),
            "inputs": [{"url": public_url}],
            "outputs": [{
                "inputSelectors": [0],
                "smartContentDescriptor": {"vocalMidi": {"mode": 1, "outputType": 1}},
            }],
        },
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_seconds, connect=20)) as client:
            response = await client.post(endpoint, json=create_payload)
            if response.is_error:
                raise CloudTranscriptionError(f"腾讯云人声转录创建失败 {response.status_code}: {response.text[:500]}")
            created = response.json()
            job = created.get("createJobResponse", {}).get("job", {})
            provider_job_id = job.get("id")
            if not provider_job_id:
                raise CloudTranscriptionError("腾讯云人声转录没有返回任务 ID。")
            interval = float(os.getenv("TENCENT_POLL_SECONDS", "2"))
            deadline = asyncio.get_running_loop().time() + timeout_seconds
            while True:
                if asyncio.get_running_loop().time() >= deadline:
                    raise CloudTranscriptionError("腾讯云人声转录等待超时。")
                await asyncio.sleep(interval)
                status_response = await client.post(endpoint, json={
                    "action": "GetJob",
                    "secretId": secret_id,
                    "secretKey": secret_key,
                    "getJobRequest": {"id": provider_job_id},
                })
                if status_response.is_error:
                    raise CloudTranscriptionError(f"腾讯云人声转录查询失败 {status_response.status_code}: {status_response.text[:500]}")
                job = status_response.json().get("getJobResponse", {}).get("job", {})
                state = int(job.get("state", 0) or 0)
                if state == 4:
                    raise CloudTranscriptionError("腾讯云人声转录任务失败。")
                if state == 5:
                    raise CloudTranscriptionError("腾讯云人声转录任务被取消。")
                if state == 3:
                    break
            result = job.get("outputs", [{}])[0].get("smartContentResult", {})
            vocal_result = result.get("vocalMidi")
            if isinstance(vocal_result, str):
                try:
                    parsed = json.loads(vocal_result)
                except json.JSONDecodeError:
                    parsed = None
                if isinstance(parsed, dict) and isinstance(parsed.get("notes"), list):
                    return _json_to_analysis(parsed)
            # Search only the completed output tree; searching the whole job
            # could accidentally select the original input URL.
            midi_url = _find_url(vocal_result) or _find_url(job.get("outputs"))
            if not midi_url:
                raise CloudTranscriptionError("腾讯云任务完成但没有找到可下载的 MIDI 文件地址。")
            midi_response = await client.get(midi_url)
            if midi_response.is_error:
                raise CloudTranscriptionError(f"腾讯云 MIDI 下载失败 {midi_response.status_code}: {midi_response.text[:500]}")
            return _midi_to_analysis(midi_response.content, source="tencent-vocal-cloud")
    except httpx.HTTPError as exc:
        raise CloudTranscriptionError(f"连接腾讯云人声转录失败：{exc}") from exc


def _find_url(value: Any) -> str | None:
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        return value
    if isinstance(value, dict):
        for nested in value.values():
            found = _find_url(nested)
            if found:
                return found
    if isinstance(value, list):
        for nested in value:
            found = _find_url(nested)
            if found:
                return found
    return None


def _midi_to_analysis(data: bytes, *, source: str = "klangio-vocal-cloud") -> dict[str, Any]:
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
        "source": source,
    }


def _json_to_analysis(result: dict[str, Any]) -> dict[str, Any]:
    notes = []
    for item in result.get("notes", []):
        try:
            pitch = int(item["pitch"])
            start = max(0.0, float(item["start"]))
            duration = float(item["duration"])
        except (KeyError, TypeError, ValueError):
            continue
        if 36 <= pitch <= 84 and duration >= 0.08:
            notes.append({
                "pitch": pitch, "pitch_cents": float(item.get("pitch_cents", 0.0)),
                "start": round(start, 3), "duration": round(duration, 3),
                "velocity": int(item.get("velocity", 88)), "confidence": float(item.get("confidence", 0.85)),
                "quantization_margin_cents": 50.0,
            })
    notes.sort(key=lambda item: item["start"])
    if len(notes) < 2:
        raise CloudTranscriptionError("腾讯云没有返回足够的单声部音符。")
    tempo = float(result.get("tempo_bpm", 100.0))
    boundaries = [
        current["start"] for previous, current in zip(notes, notes[1:])
        if current["start"] - (previous["start"] + previous["duration"]) >= 0.28
    ]
    return {
        "tempo_bpm": round(float(max(50, min(200, tempo))), 2),
        "notes": notes,
        "pitch_trace": [],
        "phrase_boundaries": boundaries,
        "pitch_contour": _classify_contour([note["pitch"] for note in notes]),
        "source": "tencent-vocal-cloud",
    }
