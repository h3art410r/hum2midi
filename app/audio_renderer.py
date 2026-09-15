"""Audio rendering backends for MIDI.

The preferred backend is FluidSynth + a user-provided SoundFont.  It keeps
MIDI semantics (programs, velocities, drums and effects) while remaining a
deterministic local render step.  The small procedural synth remains a
fallback so the demo still starts on a clean checkout.
"""

from __future__ import annotations

import logging
import math
import os
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path

from app.synth import SAMPLE_RATE, render_midi

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent.parent


def _first_existing(candidates: list[Path]) -> Path | None:
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def find_fluidsynth() -> Path | None:
    configured = os.getenv("FLUIDSYNTH_BIN", "").strip()
    if configured:
        path = Path(configured)
        if path.is_file():
            return path
        resolved = shutil.which(configured)
        if resolved:
            return Path(resolved)
    resolved = shutil.which("fluidsynth")
    if resolved:
        return Path(resolved)
    return _first_existing([
        ROOT / "data" / "fluidsynth" / "fluidsynth-v2.6.0-win10-x64-cpp11" / "bin" / "fluidsynth.exe",
        ROOT / "data" / "fluidsynth" / "bin" / "fluidsynth.exe",
    ])


def find_soundfont() -> Path | None:
    configured = os.getenv("SOUNDFONT_PATH", "").strip()
    if configured:
        path = Path(configured)
        if path.is_file():
            return path
    return _first_existing([
        ROOT / "data" / "soundfonts" / "GeneralUser-GS.sf2",
        ROOT / "data" / "soundfonts" / "FluidR3_GM.sf2",
        ROOT / "data" / "soundfonts" / "FluidR3_GS.sf2",
    ])


def renderer_status() -> str:
    mode = os.getenv("AUDIO_RENDERER", "auto").strip().lower()
    if mode == "procedural":
        return "procedural-midi-synth"
    if find_fluidsynth() and find_soundfont():
        return "fluidsynth-soundfont"
    return "procedural-midi-synth"


def render_audio(midi_path: Path, style: str, output_path: Path) -> str:
    """Render MIDI and return the backend name used.

    ``AUDIO_RENDERER=fluid synth`` (or ``soundfont``) makes a missing or
    broken SoundFont an explicit error.  ``auto`` uses it when available and
    logs a visible fallback otherwise.
    """
    mode = os.getenv("AUDIO_RENDERER", "auto").strip().lower()
    if mode in {"fluidsynth", "soundfont", "fluid-synth"} or mode == "auto":
        fluidsynth = find_fluidsynth()
        soundfont = find_soundfont()
        if fluidsynth and soundfont:
            try:
                _render_with_fluidsynth(fluidsynth, soundfont, midi_path, style, output_path)
                logger.info("audio_renderer=fluidsynth soundfont=%s style=%s", soundfont.name, style)
                return "fluidsynth-soundfont"
            except Exception:
                logger.exception("fluidsynth_render_failed style=%s", style)
                if mode != "auto":
                    raise
        elif mode != "auto":
            raise RuntimeError("FluidSynth and a SoundFont are required when AUDIO_RENDERER=soundfont")
    render_midi(midi_path, style, output_path)
    logger.info("audio_renderer=procedural-midi-synth style=%s", style)
    return "procedural-midi-synth"


def _render_with_fluidsynth(
    executable: Path,
    soundfont: Path,
    midi_path: Path,
    style: str,
    output_path: Path,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix="h2m-fs-", suffix=".wav", delete=False, dir=output_path.parent) as handle:
        temp_path = Path(handle.name)
    try:
        command = [
            str(executable), "-ni", "-F", str(temp_path), "-r", str(SAMPLE_RATE),
            "-o", "synth.reverb.active=1", "-o", "synth.chorus.active=1",
            str(soundfont), str(midi_path),
        ]
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=int(os.getenv("FLUIDSYNTH_TIMEOUT_SECONDS", "45")),
            check=False,
        )
        if completed.returncode != 0 or not temp_path.is_file() or temp_path.stat().st_size < 64:
            detail = (completed.stderr or completed.stdout or "unknown FluidSynth error").strip()[-500:]
            raise RuntimeError(f"FluidSynth exited with {completed.returncode}: {detail}")
        _master_soundfont_wav(temp_path, output_path, style)
    finally:
        temp_path.unlink(missing_ok=True)


def _master_soundfont_wav(source: Path, output: Path, style: str) -> None:
    """Convert FluidSynth's stereo PCM to phone-friendly mono PCM."""
    with wave.open(str(source), "rb") as wav:
        channels = wav.getnchannels()
        width = wav.getsampwidth()
        rate = wav.getframerate()
        frames = wav.readframes(wav.getnframes())
    if width != 2 or rate != SAMPLE_RATE:
        raise RuntimeError(f"Unsupported FluidSynth WAV format: {rate}Hz/{width * 8}bit")
    values: list[float] = []
    step = width * channels
    for index in range(0, len(frames), step):
        if channels == 1:
            sample = int.from_bytes(frames[index:index + 2], "little", signed=True) / 32768.0
        else:
            left = int.from_bytes(frames[index:index + 2], "little", signed=True) / 32768.0
            right = int.from_bytes(frames[index + 2:index + 4], "little", signed=True) / 32768.0
            sample = (left + right) * 0.5
        values.append(sample)

    if style == "lofi":
        filtered = 0.0
        delay = int(0.18 * SAMPLE_RATE)
        for index, sample in enumerate(values):
            filtered += 0.105 * (sample - filtered)
            values[index] = filtered + (values[index - delay] * 0.12 if index >= delay else 0.0)
            values[index] += 0.00045 * math.sin((index + 17) * 0.173)
    elif style == "funk":
        previous = 0.0
        for index, sample in enumerate(values):
            transient = sample - previous
            values[index] = math.tanh((sample + transient * 0.18) * 1.18) / math.tanh(1.18)
            previous = sample

    peak = max((abs(value) for value in values), default=0.0)
    if peak:
        gain = min(0.82 / peak, 2.5)
        values = [value * gain for value in values]
    output.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(output), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        pcm = bytearray()
        for value in values:
            pcm.extend(int(max(-0.95, min(0.95, value)) * 32767).to_bytes(2, "little", signed=True))
        wav.writeframes(pcm)
