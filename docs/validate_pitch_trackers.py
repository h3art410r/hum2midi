"""Cross-check note pitches/onsets with independent classical DSP methods.

This offline audit combines librosa YIN/pYIN, harmonic spectral salience, and
spectral-flux onset cues. It does not run in the application path. The isolated
Basic Pitch environment already contains librosa; no runtime dependency is added.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import wave
from pathlib import Path

import librosa
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_AUDIO = ROOT / "data/demo/model-compare/audio_16k.wav"
DEFAULT_IR = ROOT / "data/demo/pitch-trim-validation/melody_ir.json"
DEFAULT_OUT = ROOT / "data/demo/pitch-trim-validation/independent-pitch-check.json"
HOP = 160
FRAME = 2048
SPECTRAL_FRAME = 2048
SPECTRAL_NFFT = 65536


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", type=Path, default=DEFAULT_AUDIO)
    parser.add_argument("--ir", type=Path, default=DEFAULT_IR)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    with wave.open(str(args.audio), "rb") as wav:
        if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (1, 2, 16000):
            raise SystemExit("Expected 16kHz mono PCM16 WAV")
        samples = np.frombuffer(wav.readframes(wav.getnframes()), "<i2").astype(np.float32) / 32768

    ir = json.loads(args.ir.read_text(encoding="utf-8"))
    midi = librosa.yin(samples, fmin=65, fmax=500, sr=16000, frame_length=FRAME, hop_length=HOP)
    probabilistic_midi, _, _ = librosa.pyin(
        samples, fmin=65, fmax=500, sr=16000, frame_length=FRAME,
        hop_length=HOP, fill_na=np.nan,
    )
    times = librosa.times_like(midi, sr=16000, hop_length=HOP)

    # Cross-check event starts against a different signal feature: positive
    # spectral flux (not the RMS gate used by the application tracker).
    spectrum = np.abs(librosa.stft(samples, n_fft=1024, hop_length=HOP))
    spectral_flux = np.maximum(0, np.diff(spectrum, axis=1)).sum(axis=0)
    flux_times = librosa.frames_to_time(np.arange(len(spectral_flux)), sr=16000, hop_length=HOP)
    spectral_frequencies = np.fft.rfftfreq(SPECTRAL_NFFT, 1 / 16000)
    candidate_f0 = np.arange(80.0, 400.0, 0.02)

    comparisons = []
    for event in ir["note_events"]:
        trim = min(0.15, max(0.025, event["duration"] * 0.25))
        mask = (times >= event["start"] + trim) & (
            times < event["start"] + event["duration"] - trim
        )

        def stable_midi(frequencies: np.ndarray) -> float | None:
            selected = frequencies[mask]
            selected = selected[np.isfinite(selected) & (selected > 0)]
            if not len(selected):
                return None
            return float(np.median(69 + 12 * np.log2(selected / 440)))

        yin_midi = stable_midi(midi)
        pyin_midi = stable_midi(probabilistic_midi)
        pyin_values = probabilistic_midi[mask]
        pyin_values = pyin_values[np.isfinite(pyin_values) & (pyin_values > 0)]
        pyin_semitones = 69 + 12 * np.log2(pyin_values / 440)
        pyin_iqr = float(np.quantile(pyin_semitones, .75) - np.quantile(pyin_semitones, .25))
        pyin_consistency = float(np.mean(np.abs(pyin_semitones - pyin_midi) <= .25))
        expected = int(event["pitch"])
        onset_mask = (flux_times >= event["start"] - 0.12) & (flux_times <= event["start"] + 0.12)
        onset_candidates = np.flatnonzero(onset_mask)
        flux_peak_time = float(flux_times[onset_candidates[np.argmax(spectral_flux[onset_candidates])]])

        stable_start = int((event["start"] + trim) * 16000)
        stable_end = int((event["start"] + event["duration"] - trim) * 16000)
        stable_audio = samples[stable_start:stable_end]
        if len(stable_audio) < SPECTRAL_FRAME:
            stable_audio = np.pad(stable_audio, (0, SPECTRAL_FRAME - len(stable_audio)))
        spectral_frames = np.lib.stride_tricks.sliding_window_view(
            stable_audio, SPECTRAL_FRAME
        )[::320]
        window = np.hanning(SPECTRAL_FRAME)
        magnitude = np.abs(np.fft.rfft(
            spectral_frames * window, n=SPECTRAL_NFFT, axis=1
        ))
        mean_magnitude = np.mean(magnitude, axis=0)
        harmonic_salience = np.zeros_like(candidate_f0)
        for harmonic in range(1, 9):
            harmonic_salience += np.interp(
                candidate_f0 * harmonic, spectral_frequencies, mean_magnitude,
                left=0.0, right=0.0,
            ) / harmonic ** 0.75
        spectral_f0 = float(candidate_f0[int(np.argmax(harmonic_salience))])
        spectral_midi = float(69 + 12 * np.log2(spectral_f0 / 440))
        comparisons.append({
            "start_seconds": event["start"],
            "spectral_flux_peak_seconds": round(flux_peak_time, 3),
            "onset_to_flux_peak_seconds": round(flux_peak_time - event["start"], 3),
            "expected_midi": expected,
            "librosa_yin_median_midi": round(yin_midi, 3) if yin_midi is not None else None,
            "librosa_yin_rounded": int(np.rint(yin_midi)) if yin_midi is not None else None,
            "librosa_pyin_median_midi": round(pyin_midi, 3) if pyin_midi is not None else None,
            "librosa_pyin_rounded": int(np.rint(pyin_midi)) if pyin_midi is not None else None,
            "librosa_pyin_iqr_semitones": round(pyin_iqr, 3),
            "librosa_pyin_fraction_within_0_25_semitones": round(pyin_consistency, 3),
            "harmonic_spectral_median_midi": round(spectral_midi, 3),
            "harmonic_spectral_rounded": int(np.rint(spectral_midi)),
        })

    yin_matches = sum(item["expected_midi"] == item["librosa_yin_rounded"] for item in comparisons)
    pyin_matches = sum(item["expected_midi"] == item["librosa_pyin_rounded"] for item in comparisons)
    harmonic_matches = sum(item["expected_midi"] == item["harmonic_spectral_rounded"] for item in comparisons)
    onset_offsets = [abs(item["onset_to_flux_peak_seconds"]) for item in comparisons]
    ordinary_intervals = [
        b["start_seconds"] - a["start_seconds"]
        for a, b in zip(comparisons, comparisons[1:])
        if b["start_seconds"] - a["start_seconds"] < 0.85
    ]
    report = {
        "method": "librosa YIN/pYIN stable-center pitches, harmonic spectral salience, and spectral-flux onsets; non-ML DSP",
        "audio_sha256": hashlib.sha256(args.audio.read_bytes()).hexdigest(),
        "librosa_version": librosa.__version__,
        "sample_rate": 16000,
        "frame_length": FRAME,
        "hop_length": HOP,
        "event_count": len(comparisons),
        "yin_rounded_matches": yin_matches,
        "pyin_rounded_matches": pyin_matches,
        "harmonic_spectral_rounded_matches": harmonic_matches,
        "independent_spectral_flux_onset_median_abs_error_seconds": round(float(np.median(onset_offsets)), 3),
        "independent_spectral_flux_onset_max_abs_error_seconds": round(float(np.max(onset_offsets)), 3),
        "median_non_phrase_note_interval_seconds": round(float(np.median(ordinary_intervals)), 3),
        "phrase_start_interval_seconds": round(float(comparisons[7]["start_seconds"] - comparisons[6]["start_seconds"]), 3),
        "inter_phrase_silence_seconds": round(float(
            comparisons[7]["start_seconds"] - (
                comparisons[6]["start_seconds"] + ir["note_events"][6]["duration"]
            )
        ), 3),
        "events": comparisons,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in (
        "audio_sha256", "librosa_version", "event_count",
        "yin_rounded_matches", "pyin_rounded_matches", "harmonic_spectral_rounded_matches",
        "independent_spectral_flux_onset_median_abs_error_seconds",
        "independent_spectral_flux_onset_max_abs_error_seconds",
        "median_non_phrase_note_interval_seconds", "phrase_start_interval_seconds",
        "inter_phrase_silence_seconds",
    )}, ensure_ascii=False))


if __name__ == "__main__":
    main()
