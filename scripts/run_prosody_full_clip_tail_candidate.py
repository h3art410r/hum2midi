"""Single-call official Prosody-only candidate with an explicit full-phrase ending."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from native.worker_client import NativeWorkerClient, WorkerConfig
from scripts.run_control_prosody_onset_aligned_full_clip_ab import active_tail_and_pitch


FOLDER = ROOT / "native/static/official"
SOURCE = FOLDER / "debug_recent_input.wav"
CFG = float(os.getenv("PROSODY_TAIL_CFG", "2"))
MODE = os.getenv("PROSODY_TAIL_MODE", "instrumental")
SYLLABLES = int(os.getenv("PROSODY_TAIL_SYLLABLES", "24"))
SOURCE_TRACK = os.getenv("PROSODY_TAIL_SOURCE_TRACK", "input")
if MODE not in {"instrumental", "wordless_vocal"}:
    raise SystemExit(f"Unknown PROSODY_TAIL_MODE: {MODE}")
if SOURCE_TRACK not in {"input", "vocals"}:
    raise SystemExit(f"Unknown PROSODY_TAIL_SOURCE_TRACK: {SOURCE_TRACK}")
OUTPUT = FOLDER / (
    f"experiment_funk_prosody_{MODE}_{SOURCE_TRACK}_cfg{CFG:g}_n{SYLLABLES}_full_clip_seed7.wav"
)
INSTRUMENTAL_PROMPT = (
    "Create an instrumental funk arrangement of the supplied hummed tune. Re-perform that exact "
    "tune as a bright clavinet lead; use the recorded hum only as a melodic guide, and let the "
    "instrument take over its melody. Preserve the melodic contour, accented rhythm, phrase "
    "entrances, short pause, and final phrase across the full piece. Build a punchy band around "
    "the lead with syncopated bass, crisp drums, wah guitar, and brief brass answers only in the "
    "pause. Keep the same tune audible through its final source note and use the full supplied "
    "duration. Do not layer the raw recording or add a different lead melody."
)
WORDLESS_VOCAL_PROMPT = (
    "Create a bold instrumental funk arrangement around the supplied hummed tune. A clean, soft, "
    "synthetic wordless humming lead must perform the same source melody from its first phrase "
    "through its final note. Double that exact tune with bright clavinet, preserving the melodic "
    "contour, accented rhythm, phrase entrances, and short pause. Build punchy drums, syncopated "
    "bass, wah guitar, and brief brass answers around the lead, especially in the pause. Keep the "
    "melodic lead audible through the end of the full supplied duration. Do not use words, layer "
    "the raw recording, or add a different lead melody."
)
PROMPT = WORDLESS_VOCAL_PROMPT if MODE == "wordless_vocal" else INSTRUMENTAL_PROMPT
LYRICS = (
    "[Verse]\n" + " ".join(["Mmm"] * SYLLABLES)
    if MODE == "wordless_vocal"
    else ""
)
NEGATIVE = (
    "Spoken words, speech, breath or mouth noise, raw microphone noise, unrelated replacement "
    "melody, missing final source phrase, early stop, fade before the last source note, long "
    "empty intro, muddy bass, harsh clipping."
)


def source_active_end(path: Path) -> float:
    audio, sr = sf.read(path, dtype="float32", always_2d=True)
    mono = audio.mean(axis=1)
    frame = round(sr * 0.02)
    usable = len(mono) // frame * frame
    rms = np.sqrt(np.mean(mono[:usable].reshape(-1, frame) ** 2, axis=1))
    threshold = max(0.002, float(np.max(np.abs(mono))) * 0.02)
    active = np.flatnonzero(rms >= threshold)
    return float((active[-1] + 1) * frame / sr) if len(active) else 0.0


def main() -> None:
    if not SOURCE.is_file():
        raise SystemExit(f"Source audio is missing: {SOURCE}")
    if OUTPUT.exists() or OUTPUT.with_suffix(".json").exists():
        raise SystemExit(f"Refusing to overwrite: {OUTPUT}")

    client = NativeWorkerClient(
        WorkerConfig(
            url="http://192.168.9.100:8765",
            timeout_seconds=900,
        )
    )
    health = client.health()
    if health.get("status") != "ok" or health.get("worker_busy"):
        raise SystemExit(f"Worker unavailable or busy: {json.dumps(health, ensure_ascii=False)}")
    if (
        health.get("supports_exact_duration") is not True
        or health.get("generation_contract") != "full_clip_exact_input_samples_v1"
    ):
        raise SystemExit(
            f"Worker build {health.get('build')} does not advertise the one-call, "
            "full-clip exact-sample contract; update the Worker before running this A/B"
        )
    supported_sources = health.get("supported_prosody_sources")
    if not isinstance(supported_sources, list) or SOURCE_TRACK not in supported_sources:
        raise SystemExit(
            f"Worker build {health.get('build')} does not advertise the full-clip "
            f"prosody_source={SOURCE_TRACK} contract; update the Worker before running this A/B"
        )

    print("START · one full-duration official Prosody-only generation", flush=True)
    diagnostics = client.generate(
        SOURCE,
        OUTPUT,
        prompt=PROMPT,
        negative_prompt=NEGATIVE,
        lyrics=LYRICS,
        seed=7,
        cfg_scale=CFG,
        steps=50,
        control="prosody",
        prosody_source=SOURCE_TRACK,
    )
    if diagnostics.get("prosody_source") != SOURCE_TRACK:
        raise SystemExit(
            f"Worker did not confirm prosody_source={SOURCE_TRACK}; refusing to score this candidate"
        )
    source_audio, source_sr = sf.read(SOURCE, dtype="float32", always_2d=True)
    output_audio, output_sr = sf.read(OUTPUT, dtype="float32", always_2d=True)
    validation = active_tail_and_pitch(SOURCE, OUTPUT, source_active_end(SOURCE))
    duration_passed = (
        source_sr == 48000
        and output_sr == source_sr
        and len(output_audio) == len(source_audio)
    )
    validation.update(
        {
            "duration_passed": duration_passed,
            "duration_error_samples": int(len(output_audio) - len(source_audio)),
            "source_duration_seconds": round(len(source_audio) / source_sr, 6),
            "output_duration_seconds": round(len(output_audio) / output_sr, 6),
        }
    )
    passed = (
        duration_passed
        and validation["activity_passed"]
        and validation["melody_tail_passed"]
    )
    record = {
        "name": f"Funk · 整段尾句保留候选 · 官方 Prosody-only · CFG{CFG:g} · seed7",
        "audio": OUTPUT.name,
        "source_audio": SOURCE.name,
        "source_samples": len(source_audio),
        "source_sample_rate": source_sr,
        "output_samples": len(output_audio),
        "output_sample_rate": output_sr,
        "duration_seconds": round(len(output_audio) / output_sr, 6),
        "seed": 7,
        "cfg_scale": CFG,
        "steps": 50,
        "control": "prosody",
        "mode": MODE,
        "prosody_source": SOURCE_TRACK,
        "lyrics": LYRICS,
        "wordless_syllable_count": SYLLABLES if MODE == "wordless_vocal" else 0,
        "prompt": PROMPT,
        "negative_prompt": NEGATIVE,
        "validation": {**validation, "passed": bool(passed)},
        "diagnostics": diagnostics,
        "worker_health": health,
        "generation_mode": "one full-clip TemplatePipeline call; no segmentation or concatenation",
        "listening_status": "pending",
    }
    OUTPUT.with_suffix(".json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"DONE mode={MODE} cfg={CFG:g} passed={passed} duration={record['duration_seconds']}s "
        f"validation={validation} file={OUTPUT.name}",
        flush=True,
    )


if __name__ == "__main__":
    main()
