"""Deterministic, non-ML pitch and onset measurements for humming.

The cloud Omni analysis remains mandatory. This signal-processing pass refines
note boundaries and fundamental pitches where the language model's timestamps
or equal-grid note guesses are not acoustically grounded.
"""
from __future__ import annotations

import wave
from pathlib import Path
from typing import Any

import numpy as np


SAMPLE_RATE = 16_000
PITCH_FRAME = 2_048
PITCH_HOP = 160
PITCH_FFT = 4_096
ENERGY_FRAME = 320
ENERGY_HOP = 160


class PitchTrackingError(RuntimeError):
    pass


def extract_hummed_notes(path: Path) -> dict[str, Any]:
    """Extract note events from voiced regions and YIN F0 without an ML model."""
    try:
        with wave.open(str(path), "rb") as audio:
            if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (1, 2, SAMPLE_RATE):
                raise PitchTrackingError("旋律分析要求 16kHz 单声道 PCM16 WAV。")
            samples = np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2").astype(np.float64) / 32768
    except (wave.Error, EOFError) as exc:
        raise PitchTrackingError("无法读取用于旋律分析的 PCM WAV。") from exc

    if len(samples) < PITCH_FRAME:
        raise PitchTrackingError("录音太短，无法提取稳定旋律。")

    samples = _voice_bandpass(samples)
    pitches, voiced, pitch_confidence = _yin_track(samples)
    rms = _short_rms(samples)
    low_energy = rms[rms <= np.quantile(rms, 0.2)]
    noise_floor = float(np.median(low_energy)) if len(low_energy) else 0.0
    peak_energy = float(np.quantile(rms, 0.95))
    # A continuous legato phrase has no true noise-only frames. In that case,
    # the lower RMS quantile is still voiced energy and must not be treated as
    # the noise floor (doing so can put the threshold above the whole signal).
    threshold = max(0.008, peak_energy * 0.06)
    if noise_floor < peak_energy * 0.25:
        threshold = max(threshold, noise_floor * 3.0)
    active = rms > threshold

    # A single low-energy analysis frame inside a held syllable is usually a
    # transient, while larger gaps mark the singer's repeated note onsets.
    if len(active) > 2:
        holes = np.flatnonzero(active[:-2] & ~active[1:-1] & active[2:]) + 1
        active[holes] = True

    regions = _regions(active)
    notes: list[dict[str, Any]] = []
    for start_frame, end_frame in regions:
        region_start = start_frame * ENERGY_HOP / SAMPLE_RATE
        region_end = min(len(samples) / SAMPLE_RATE, end_frame * ENERGY_HOP / SAMPLE_RATE + ENERGY_FRAME / SAMPLE_RATE)
        for start, end in _split_at_pitch_changes(region_start, region_end, pitches, voiced):
            if end - start < 0.12:
                continue

            # Pitch often glides into and out of a hummed note. Measure the
            # stable center, excluding at most 150ms from either edge.
            edge_trim = min(0.15, max(0.025, (end - start) * 0.25))
            center_start = start + edge_trim
            center_end = end - edge_trim
            first = max(0, int(center_start * SAMPLE_RATE / PITCH_HOP))
            last = min(len(pitches), int(np.ceil(center_end * SAMPLE_RATE / PITCH_HOP)))
            indices = np.arange(first, last)
            indices = indices[voiced[indices] & (pitches[indices] > 0)]
            if len(indices) < 4:
                continue

            continuous_pitch = float(np.median(pitches[indices]))
            pitch = int(np.rint(continuous_pitch))
            if not 36 <= pitch <= 84:
                continue
            spread = float(np.median(np.abs(pitches[indices] - continuous_pitch)))
            f0_conf = float(np.median(pitch_confidence[indices]))
            confidence = float(np.clip(f0_conf * np.exp(-spread / 1.5), 0.1, 0.98))
            notes.append({
                "pitch": pitch,
                # Keep the measured fractional semitone so the MIDI writer
                # can add a fine pitch bend instead of throwing away up to
                # 50 cents at integer quantization.
                "pitch_cents": round(float((continuous_pitch - pitch) * 100), 1),
                "quantization_margin_cents": round(
                    max(0.0, 0.5 - abs(continuous_pitch - pitch)) * 100, 1
                ),
                "start": round(start, 3),
                "duration": round(end - start, 3),
                "velocity": 88,
                "confidence": round(confidence, 3),
            })

    notes = _merge_same_pitch_fragments(notes)
    if len(notes) < 2:
        raise PitchTrackingError("没有检测到足够清晰的连续哼唱音符。")

    pitch_trace = [
        {
            "time": round((index * PITCH_HOP + PITCH_FRAME / 2) / SAMPLE_RATE, 3),
            "pitch": round(float(pitches[index]), 2),
        }
        for index in range(0, len(pitches), 5)
        if voiced[index] and pitches[index] > 0
    ]

    intervals = [
        notes[i + 1]["start"] - notes[i]["start"]
        for i in range(len(notes) - 1)
        if 0.18 <= notes[i + 1]["start"] - notes[i]["start"] <= 0.85
    ]
    tempo = 60.0 / float(np.median(intervals)) if intervals else 100.0
    phrase_boundaries = [
        note["start"] for previous, note in zip(notes, notes[1:])
        if note["start"] - (previous["start"] + previous["duration"]) >= 0.28
    ]
    return {
        "tempo_bpm": round(float(np.clip(tempo, 50, 200)), 2),
        "notes": notes,
        "pitch_trace": pitch_trace,
        "phrase_boundaries": phrase_boundaries,
        "pitch_contour": _classify_contour([note["pitch"] for note in notes]),
        "source": "qwen-omni-contour+deterministic-yin",
    }


def _short_rms(samples: np.ndarray) -> np.ndarray:
    if len(samples) < ENERGY_FRAME:
        return np.empty(0, dtype=np.float64)
    frames = np.lib.stride_tricks.sliding_window_view(samples, ENERGY_FRAME)[::ENERGY_HOP]
    return np.sqrt(np.mean(frames * frames, axis=1))


def _voice_bandpass(samples: np.ndarray) -> np.ndarray:
    """Reduce rumble and broadband hiss while retaining hum F0 and harmonics."""
    frequencies = np.fft.rfftfreq(len(samples), 1 / SAMPLE_RATE)
    high_pass = np.clip((frequencies - 50) / 30, 0, 1)
    low_pass = np.clip((1200 - frequencies) / 400, 0, 1)
    return np.fft.irfft(np.fft.rfft(samples) * high_pass * low_pass, n=len(samples))


def _regions(active: np.ndarray) -> list[tuple[int, int]]:
    regions: list[tuple[int, int]] = []
    start: int | None = None
    for i, value in enumerate(np.append(active, False)):
        if value and start is None:
            start = i
        elif not value and start is not None:
            regions.append((start, i))
            start = None
    return regions


def _merge_same_pitch_fragments(notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge a tiny boundary fragment back into an adjacent same-pitch note.

    Low-energy singers often produce one extra 150–250ms segment at the end
    of a held note when the F0 tracker briefly crosses a boundary. Only merge
    adjacent equal MIDI pitches with a near-zero gap and a clearly short
    fragment; intentional repeated notes and fast melodies remain separate.
    """
    merged: list[dict[str, Any]] = []
    for current in notes:
        if not merged:
            merged.append(current)
            continue
        previous = merged[-1]
        previous_end = float(previous.get("start", 0.0)) + float(previous.get("duration", 0.0))
        gap = float(current.get("start", 0.0)) - previous_end
        same_pitch = int(previous.get("pitch", -1)) == int(current.get("pitch", -2))
        short_fragment = min(float(previous.get("duration", 0.0)), float(current.get("duration", 0.0))) < 0.30
        if same_pitch and -0.015 <= gap <= 0.08 and short_fragment:
            previous_duration = float(previous.get("duration", 0.0))
            current_duration = float(current.get("duration", 0.0))
            total_duration = previous_duration + max(0.0, gap) + current_duration
            if total_duration > 0:
                previous["pitch_cents"] = round(
                    (float(previous.get("pitch_cents", 0.0)) * previous_duration
                     + float(current.get("pitch_cents", 0.0)) * current_duration)
                    / (previous_duration + current_duration),
                    1,
                )
            previous["duration"] = round(total_duration, 3)
            previous["confidence"] = round(min(
                float(previous.get("confidence", 0.5)),
                float(current.get("confidence", 0.5)),
            ), 3)
            previous["quantization_margin_cents"] = round(
                max(0.0, 50.0 - abs(float(previous.get("pitch_cents", 0.0)))), 1
            )
        else:
            merged.append(current)
    return merged


def _split_at_pitch_changes(
    start: float,
    end: float,
    pitches: np.ndarray,
    voiced: np.ndarray,
) -> list[tuple[float, float]]:
    """Split continuous voiced regions at stable F0 steps (legato notes)."""
    first = max(0, int(start * SAMPLE_RATE / PITCH_HOP))
    last = min(len(pitches), int(np.ceil(end * SAMPLE_RATE / PITCH_HOP)))
    local_pitch = pitches[first:last]
    local_voiced = voiced[first:last] & (local_pitch > 0)
    if len(local_pitch) < 28 or np.count_nonzero(local_voiced) < 24:
        return [(start, end)]

    # Compare robust 100ms windows on either side. The overlapping candidates
    # around one transition collapse to a single boundary; small vibrato stays
    # below the 0.65-semitone threshold.
    candidates: list[int] = []
    for index in range(12, len(local_pitch) - 12):
        before_mask = local_voiced[index - 12:index - 2]
        after_mask = local_voiced[index + 2:index + 12]
        if np.count_nonzero(before_mask) < 7 or np.count_nonzero(after_mask) < 7:
            continue
        before = float(np.median(local_pitch[index - 12:index - 2][before_mask]))
        after = float(np.median(local_pitch[index + 2:index + 12][after_mask]))
        if abs(after - before) >= 0.65:
            candidates.append(index)

    clusters: list[list[int]] = []
    for index in candidates:
        if clusters and index - clusters[-1][-1] <= 5:
            clusters[-1].append(index)
        else:
            clusters.append([index])

    boundaries: list[float] = []
    for cluster in clusters:
        index = int(round(float(np.median(cluster))))
        # YIN frames describe a window centered 64ms after their nominal frame
        # start; shift the detected transition back to the acoustic boundary.
        boundary = (first + index) * PITCH_HOP / SAMPLE_RATE + PITCH_FRAME / (2 * SAMPLE_RATE)
        if boundary - start >= 0.20 and end - boundary >= 0.20:
            if not boundaries or boundary - boundaries[-1] >= 0.20:
                boundaries.append(boundary)
    cuts = [start, *boundaries, end]
    return list(zip(cuts, cuts[1:]))


def _yin_track(samples: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    frames = np.lib.stride_tricks.sliding_window_view(samples, PITCH_FRAME)[::PITCH_HOP].copy()
    frames -= frames.mean(axis=1, keepdims=True)
    rms = np.sqrt(np.mean(frames * frames, axis=1))
    spectrum = np.fft.rfft(frames, n=PITCH_FFT, axis=1)
    autocorrelation = np.fft.irfft(spectrum * spectrum.conj(), n=PITCH_FFT, axis=1)[:, :PITCH_FRAME]
    cumulative_energy = np.pad(np.cumsum(frames * frames, axis=1), ((0, 0), (1, 0)))
    lag = np.arange(1, 241)
    difference = (
        cumulative_energy[:, PITCH_FRAME - lag]
        + cumulative_energy[:, -1, None]
        - cumulative_energy[:, lag]
        - 2 * autocorrelation[:, lag]
    )
    difference = np.maximum(difference, 1e-12)
    cmnd = difference * lag[None, :] / np.maximum(np.cumsum(difference, axis=1), 1e-12)

    f0 = np.zeros(len(frames), dtype=np.float64)
    confidence = np.zeros(len(frames), dtype=np.float64)
    for i, row in enumerate(cmnd):
        band = row[19:229]
        below = np.flatnonzero(band < 0.18)
        k = 19 + int(below[0]) if below.size else 19 + int(np.argmin(band))
        if not below.size and row[k] > 0.32:
            continue
        while k + 1 < 229 and row[k + 1] < row[k]:
            k += 1
        tau = float(k + 1)
        if 0 < k < len(row) - 1:
            a, b, c = row[k - 1:k + 2]
            denominator = a - 2 * b + c
            if abs(denominator) > 1e-12:
                tau += 0.5 * (a - c) / denominator
        f0[i] = SAMPLE_RATE / tau
        confidence[i] = 1.0 - row[k]

    voiced = (rms > max(0.008, float(np.quantile(rms, 0.2)) * 0.45)) & (f0 > 0) & (confidence >= 0.68)
    midi = np.zeros(len(f0), dtype=np.float64)
    midi[voiced] = 69 + 12 * np.log2(f0[voiced] / 440)
    padded = np.pad(midi, (5, 5), mode="edge")
    smoothed = np.median(np.lib.stride_tricks.sliding_window_view(padded, 11), axis=1)
    midi[voiced] = smoothed[voiced]
    return np.clip(midi, 36, 84), voiced, confidence


def _classify_contour(pitches: list[int]) -> str:
    if len(pitches) < 4:
        return "uncertain"
    peak_index = int(np.argmax(pitches))
    if 1 <= peak_index <= len(pitches) - 2:
        rising = np.median(pitches[:peak_index + 1]) < pitches[peak_index] - 1
        falling = np.median(pitches[peak_index:]) < pitches[peak_index] - 1
        if rising and falling:
            return "ascending_then_descending"
    trough_index = int(np.argmin(pitches))
    if 1 <= trough_index <= len(pitches) - 2:
        falling = np.median(pitches[:trough_index + 1]) > pitches[trough_index] + 1
        rising = np.median(pitches[trough_index:]) > pitches[trough_index] + 1
        if falling and rising:
            return "descending_then_ascending"
    if abs(pitches[-1] - pitches[0]) <= 2 and max(pitches) - min(pitches) <= 3:
        return "mostly_flat"
    return "uncertain"
