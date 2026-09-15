"""Run the real HTTP demo flow against a running FastAPI server.

Usage:
    python docs/e2e_http_test.py path/to/hum.m4a --base-url http://127.0.0.1:8000
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import httpx

# Allow the documented `python docs/e2e_http_test.py ...` invocation from the
# project root (Python otherwise puts only `docs/` on sys.path).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.ir import midi_summary


async def run(audio_path: Path, base_url: str) -> dict[str, object]:
    async with httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=30) as client:
        health = await client.get("/api/health")
        health.raise_for_status()
        created = await client.post(
            "/api/generations",
            content=audio_path.read_bytes(),
            headers={"content-type": "audio/mp4", "x-audio-filename": "hum.m4a"},
        )
        created.raise_for_status()
        job_id = created.json()["id"]
        status: dict[str, object] = {}
        for _ in range(300):
            await asyncio.sleep(0.5)
            response = await client.get(f"/api/generations/{job_id}")
            response.raise_for_status()
            status = response.json()
            if status.get("status") in {"completed", "failed"}:
                break
        job_dir = Path("data/demo") / str(job_id)
        midi = midi_summary((job_dir / "melody.mid").read_bytes()) if (job_dir / "melody.mid").is_file() else None
        audio_routes: dict[str, object] = {}
        style_midi: dict[str, object] = {}
        for style in ("funk", "lofi"):
            result = await client.get(f"/api/generations/{job_id}/audio/{style}")
            audio_routes[style] = {"status": result.status_code, "bytes": len(result.content)}
            styled_path = job_dir / f"{style}.mid"
            if styled_path.is_file():
                styled_summary = midi_summary(styled_path.read_bytes())
                style_midi[style] = {"note_count": len(styled_summary["note_events"]), "tempo_bpm": styled_summary["tempo_bpm"]}
        melody = status.get("melody") if isinstance(status.get("melody"), dict) else {}
        return {
            "job_id": job_id,
            "status": status.get("status"),
            "error": status.get("error"),
            "note_count": len(melody.get("note_events", [])),
            "pitch_sequence": [note["pitch"] for note in melody.get("note_events", [])],
            "phrase_boundaries": melody.get("phrase_boundaries"),
            "tempo_bpm": melody.get("tempo_bpm"),
            "midi_roundtrip_count": len(midi["note_events"]) if midi else None,
            "style_midi": style_midi,
            "audio_routes": audio_routes,
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("audio", type=Path)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    if not args.audio.is_file():
        raise SystemExit(f"Audio file not found: {args.audio}")
    print(json.dumps(asyncio.run(run(args.audio, args.base_url)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
