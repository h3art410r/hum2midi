"""Tiny deterministic MIDI renderer for the demo (algorithmic synthesis, no ML)."""

from __future__ import annotations

import math
import wave
from pathlib import Path

import mido


SAMPLE_RATE = 22050


def _pluck_wave(frequency: float, count: int, seed: int) -> list[float]:
    """Small deterministic Karplus–Strong string used for Funk guitar.

    It is intentionally self-contained: no model weights or external sample
    files are needed, while the filtered noise loop gives the attack a less
    synthetic character than a stack of stationary sine waves.
    """
    if count <= 0:
        return []
    delay = max(2, min(int(SAMPLE_RATE / max(35.0, frequency)), 1800))
    state = seed & 0xFFFFFFFF
    ring: list[float] = []
    for _ in range(delay):
        state = (state * 1664525 + 1013904223) & 0xFFFFFFFF
        ring.append((state / 2147483648.0) - 1.0)
    output: list[float] = []
    index = 0
    for _ in range(count):
        current = ring[index]
        next_value = ring[(index + 1) % delay]
        ring[index] = (current + next_value) * 0.497
        output.append(current)
        index = (index + 1) % delay
    return output


def render_midi(midi_path: Path, style: str, output_path: Path) -> None:
    midi = mido.MidiFile(midi_path)
    tempo = 500_000
    elapsed = 0.0
    notes = []
    programs: dict[int, int] = {}
    active: dict[tuple[int, int], list[tuple[float, int, int]]] = {}
    for msg in midi.merged_track:
        elapsed += mido.tick2second(msg.time, midi.ticks_per_beat, tempo)
        if msg.type == "set_tempo":
            tempo = msg.tempo
        elif msg.type == "program_change":
            programs[msg.channel] = msg.program
        elif msg.type == "note_on" and msg.velocity > 0:
            active.setdefault((msg.channel, msg.note), []).append((elapsed, msg.velocity, programs.get(msg.channel, 0)))
        elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
            starts = active.get((msg.channel, msg.note))
            if starts:
                start, velocity, program = starts.pop(0)
                notes.append((msg.note, start, max(0.04, elapsed - start), velocity, msg.channel, program))
    if not notes:
        raise ValueError("MIDI contains no notes")

    # The text model often represents harmony as compact root notes. Expand
    # those roots into quiet chord tones at render time so the WAV has harmonic
    # body while the exported MIDI remains exactly the model's arrangement.
    harmonic_notes = []
    for note in notes:
        harmonic_notes.append(note)
        pitch, start, length, velocity, channel, program = note
        if channel == 1 and 0 <= program <= 7:
            for interval, gain in ((4, 0.34), (7, 0.28), (10, 0.18)):
                if pitch + interval <= 127:
                    harmonic_notes.append((pitch + interval, start, length, int(velocity * gain), channel, program))
    notes = harmonic_notes

    # Nudge near-offbeat events into a gentle pocket. The model still decides
    # the arrangement; this only turns a perfectly square grid into a more
    # playable Funk/Lofi feel at render time.
    beat_seconds = max(0.2, tempo / 1_000_000.0)
    swing = 0.055 if style == "funk" else 0.032 if style == "lofi" else 0.0
    if swing:
        swung = []
        for pitch, start, length, velocity, channel, program in notes:
            phase_in_beat = (start / beat_seconds) % 1.0
            # Keep the lead's measured onset grid exact.  Pocket/swing belongs
            # to the generated accompaniment and drums, otherwise the source
            # hummed rhythm becomes harder to recognize.
            if channel != 0 and 0.40 <= phase_in_beat <= 0.60:
                start += swing
            swung.append((pitch, start, length, velocity, channel, program))
        notes = swung

    duration = max(start + length for _, start, length, _, _, _ in notes)
    total_seconds = duration + 1.0
    sample_count = int(total_seconds * SAMPLE_RATE)
    mix = [0.0] * sample_count

    # Lead notes come directly from the MIDI. Plain mode is a dry sine reference.
    for pitch, start, length, velocity, channel, program in notes:
        frequency = 440.0 * (2 ** ((pitch - 69) / 12))
        begin = int(start * SAMPLE_RATE)
        count = min(int(length * SAMPLE_RATE), sample_count - begin)
        pluck = _pluck_wave(frequency, count, begin + pitch * 17) if style == "funk" and 24 <= program <= 31 else None
        layer_gain = (
            1.0 if channel == 0
            else (0.82 if style == "funk" else 0.92) if channel == 1
            else (0.62 if style == "funk" else 0.64) if channel == 2
            else (1.65 if style == "funk" else 0.68) if channel == 9
            else 0.3
        )
        amplitude = (velocity / 127) * layer_gain * (0.22 if style == "funk" else 0.13 if style == "lofi" else 0.15)
        for offset in range(count):
            t = offset / SAMPLE_RATE
            attack = min(1.0, t / 0.018)
            release = min(1.0, max(0.0, (length - t) / (0.055 if style == "funk" else 0.12)))
            phase = 2 * math.pi * frequency * t
            # Deterministic white-ish noise for drums/tape.  The previous
            # product of two sines had a strong periodic pitch and contributed
            # to the chiptune impression in blind listening.
            noise_state = ((begin + offset + 1) * 1664525 + 1013904223) & 0xFFFFFFFF
            noise = (noise_state / 2147483648.0) - 1.0
            if channel == 9:
                # GM drum notes: give the model's rhythm track recognizable
                # kick/snare/hat voices instead of treating every hit as a
                # pitched sine wave.
                if pitch in {35, 36}:  # acoustic/electric bass drum
                    sweep = 125.0 - 70.0 * min(1.0, t * 7.0)
                    sample = math.sin(2 * math.pi * sweep * t) * math.exp(-t * 15)
                elif pitch in {37, 38, 40}:  # rim/snare
                    sample = (0.72 * noise + 0.28 * math.sin(2 * math.pi * 185 * t)) * math.exp(-t * 25)
                elif pitch in {42, 44, 46}:  # closed/open hi-hat
                    sample = (0.84 * noise + 0.16 * math.sin(2 * math.pi * 8200 * t)) * math.exp(-t * (55 if pitch != 46 else 24))
                else:  # clap, tom, or cymbal-like accent
                    sample = (0.55 * noise + 0.45 * math.sin(2 * math.pi * 420 * t)) * math.exp(-t * 18)
            elif 24 <= program <= 31:  # guitar family
                pick = math.exp(-t * 32) * (0.13 * math.sin(phase * 4) + 0.045 * noise)
                wah = 1.0 + 0.12 * math.sin(2 * math.pi * 2.2 * t)
                sample = (
                    0.56 * math.sin(phase)
                    + 0.20 * math.sin(phase * 2)
                    + 0.07 * math.sin(phase * 3)
                    + (0.26 * pluck[offset] if pluck is not None else 0.0)
                    + wah * pick
                ) / 0.97
            elif 32 <= program <= 39:  # bass family
                slap = math.exp(-t * 34) * (0.16 * math.sin(phase * 3) + 0.06 * noise)
                if style == "funk":
                    sample = 0.62 * math.sin(phase) + 0.24 * math.sin(phase * 2) + 0.10 * math.sin(phase * 3) + slap
                else:
                    sample = 0.78 * math.sin(phase) + 0.14 * math.sin(phase * 2) + slap
            elif program == 7 and style == "funk":  # clavinet / funk keys
                # A short, slightly nasal clav envelope leaves room for the
                # lead and makes offbeat chord stabs read as a real Funk part.
                decay = math.exp(-t * 15)
                bite = math.exp(-t * 42) * (0.18 * math.sin(phase * 4) + 0.04 * noise)
                sample = decay * (
                    0.66 * math.sin(phase)
                    + 0.25 * math.sin(phase * 2)
                    + 0.09 * math.sin(phase * 3)
                ) + bite
            elif 0 <= program <= 7:  # piano/keyboard family
                hammer = math.exp(-t * 20)
                tremolo = 1.0 + 0.018 * math.sin(2 * math.pi * 4.7 * t)
                sample = tremolo * (
                    math.sin(phase)
                    + 0.19 * math.sin(phase * 2.01)
                    + 0.06 * math.sin(phase * 3.02)
                    + 0.025 * noise * hammer
                ) * (0.82 + 0.18 * hammer)
            elif 80 <= program <= 87:  # lead synth family
                vibrato = 1.0 + 0.004 * math.sin(2 * math.pi * 5.2 * t)
                # A rounded, mildly nasal lead reads as clav/electric keys;
                # avoid a hard square/saw edge that sounds like chiptune.
                phase_v = phase * vibrato
                pluck = math.exp(-t * 28) * (0.12 * math.sin(phase_v * 4) + 0.035 * noise)
                sample = (
                    0.68 * math.sin(phase_v)
                    + 0.23 * math.sin(phase_v * 2)
                    + 0.11 * math.sin(phase_v * 3)
                    + pluck
                ) / 1.05
            elif 88 <= program <= 95:  # warm pad family (used by Lofi harmony)
                # Slow attack and a pair of detuned partials produce a soft
                # bed without masking the source lead's note attacks.
                pad_attack = min(1.0, t / 0.12)
                detune = 1.0 + 0.0018 * math.sin(2 * math.pi * 0.7 * t)
                sample = pad_attack * (
                    0.62 * math.sin(phase * detune)
                    + 0.22 * math.sin(phase * 1.006)
                    + 0.11 * math.sin(phase * 2)
                ) / 0.95
            elif channel == 1:
                sample = 0.9 * math.sin(phase) + 0.08 * math.sin(phase * 2)
            elif channel == 2:
                sample = 0.7 * math.sin(phase) + 0.18 * math.sin(phase * 2) + 0.06 * math.sin(phase * 3)
            elif style == "funk":
                sample = (math.sin(phase) + 0.24 * math.sin(phase * 2) + 0.08 * math.sin(phase * 3)) / 1.32
            elif style == "plain":
                sample = math.sin(phase)
            else:
                sample = 0.82 * math.sin(phase) + 0.12 * math.sin(phase * 2)
            mix[begin + offset] += amplitude * attack * release * sample

    # Small style-specific bus treatment keeps the deterministic renderer
    # musical without adding another model or external audio dependency.
    if style == "funk":
        # A restrained high-shelf exciter puts the pick and snare attacks in
        # front of the mix.  This is the bright, percussive contrast to Lofi's
        # deliberately dark tape bus; it does not alter note timing.
        previous = 0.0
        for index, value in enumerate(mix):
            transient = value - previous
            mix[index] = value + transient * 0.42
            previous = value
        delay = int(0.085 * SAMPLE_RATE)
        for index in range(delay, sample_count):
            mix[index] += mix[index - delay] * 0.16
        # Gentle console-style saturation makes the clav/lead and drum bus
        # read as a funk performance instead of thin sine tones.
        drive = math.tanh(1.65)
        for index, value in enumerate(mix):
            mix[index] = math.tanh(value * 1.65) / drive
    elif style == "lofi":
        # One-pole low-pass plus a quiet tape-like echo.
        filtered = 0.0
        alpha = 0.12
        delay = int(0.18 * SAMPLE_RATE)
        for index in range(sample_count):
            filtered += alpha * (mix[index] - filtered)
            mix[index] = filtered
            if index >= delay:
                mix[index] += mix[index - delay] * 0.14
            # Deterministic, very quiet tape hiss; it is masked by notes but
            # keeps held chords from sounding digitally empty.
            # Keep the tape bed audible under held chords but quiet enough
            # that a source phrase pause still reads as a real breath.
            mix[index] += 0.0007 * math.sin((index + 17) * 0.173) * math.sin((index + 3) * 0.037)
            # Slow wow/flutter at a deliberately inaudible pitch excursion;
            # the amplitude movement is enough to distinguish the tape bus.
            mix[index] *= 0.992 + 0.008 * math.sin(2 * math.pi * 0.42 * index / SAMPLE_RATE)

    # Master the demo output to a phone-friendly level without hard clipping.
    peak = max((abs(value) for value in mix), default=0.0)
    if peak > 0.0:
        gain = 0.78 / peak
        for index, value in enumerate(mix):
            mix[index] = value * gain

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(output_path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        pcm = bytearray()
        for value in mix:
            clipped = max(-0.95, min(0.95, value))
            pcm.extend(int(clipped * 32767).to_bytes(2, "little", signed=True))
        wav.writeframes(pcm)
