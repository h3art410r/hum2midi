import math
import tempfile
import unittest
import wave
from array import array
from pathlib import Path

import numpy as np

from app.pitch_tracking import _classify_contour, _merge_same_pitch_fragments, extract_hummed_notes
from app.ir import melody_to_midi, midi_summary


class PitchTrackingTests(unittest.TestCase):
    def test_extracts_pitch_onset_and_duration_from_clean_control_audio(self):
        sample_rate = 16_000
        expected = [(57, 0.5, 0.6), (64, 1.3, 0.8), (60, 2.4, 0.5), (67, 3.2, 1.0), (59, 4.6, 0.7)]
        samples = array("h", [0]) * (sample_rate * 6)
        for pitch, start, duration in expected:
            frequency = 440 * 2 ** ((pitch - 69) / 12)
            offset = round(start * sample_rate)
            for i in range(round(duration * sample_rate)):
                t = i / sample_rate
                envelope = min(1.0, t / 0.02, (duration - t) / 0.02)
                samples[offset + i] = round(12_000 * envelope * math.sin(2 * math.pi * frequency * t))

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "control.wav"
            with wave.open(str(path), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(sample_rate)
                audio.writeframes(samples.tobytes())
            result = extract_hummed_notes(path)

        self.assertEqual([note["pitch"] for note in result["notes"]], [note[0] for note in expected])
        for actual, (_, start, duration) in zip(result["notes"], expected):
            self.assertLessEqual(abs(actual["start"] - start), 0.04)
            self.assertLessEqual(abs(actual["duration"] - duration), 0.06)

    def test_real_hum_yields_two_phrases_and_matching_global_contour(self):
        audio = Path(__file__).resolve().parents[1] / "data/demo/model-compare/audio_16k.wav"
        if not audio.exists():
            self.skipTest("Local evaluation recording is not available")

        result = extract_hummed_notes(audio)

        self.assertEqual(len(result["notes"]), 14)
        self.assertEqual(result["phrase_boundaries"], [5.64])
        self.assertEqual(result["pitch_contour"], "ascending_then_descending")
        self.assertEqual([n["pitch"] for n in result["notes"][:7]], [44, 44, 52, 52, 54, 54, 52])
        self.assertEqual([n["pitch"] for n in result["notes"][7:]], [50, 49, 48, 47, 46, 45, 44])

    def test_real_hum_survives_lower_gain_and_room_noise(self):
        audio = Path(__file__).resolve().parents[1] / "data/demo/model-compare/audio_16k.wav"
        if not audio.exists():
            self.skipTest("Local evaluation recording is not available")
        clean = extract_hummed_notes(audio)
        with wave.open(str(audio), "rb") as source:
            rate = source.getframerate()
            samples = np.frombuffer(source.readframes(source.getnframes()), "<i2").astype(float) / 32768
        rng = np.random.default_rng(7)
        noisy = np.clip(0.35 * samples + rng.normal(0, 0.015, len(samples)), -1, 1)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "noisy-hum.wav"
            with wave.open(str(path), "wb") as target:
                target.setnchannels(1)
                target.setsampwidth(2)
                target.setframerate(rate)
                target.writeframes((noisy * 32767).astype("<i2").tobytes())
            result = extract_hummed_notes(path)

        self.assertEqual([note["pitch"] for note in result["notes"]], [44, 44, 52, 52, 54, 54, 52, 50, 49, 48, 47, 46, 45, 44])
        self.assertEqual(result["pitch_contour"], "ascending_then_descending")
        self.assertLess(abs(result["tempo_bpm"] - 102.56), 2)
        self.assertLess(abs(result["phrase_boundaries"][0] - 5.64), 0.05)
        self.assertLess(max(abs(a["start"] - b["start"]) for a, b in zip(result["notes"], clean["notes"])), 0.03)

    def test_real_hum_midi_roundtrip_preserves_pitch_and_rhythm(self):
        audio = Path(__file__).resolve().parents[1] / "data/demo/model-compare/audio_16k.wav"
        if not audio.exists():
            self.skipTest("Local evaluation recording is not available")
        analysis = extract_hummed_notes(audio)
        midi_bytes, ir = melody_to_midi(analysis)
        parsed = midi_summary(midi_bytes)

        self.assertEqual([note["pitch"] for note in parsed["note_events"]], [
            44, 44, 52, 52, 54, 54, 52, 50, 49, 48, 47, 46, 45, 44,
        ])
        self.assertEqual(len(parsed["note_events"]), 14)
        self.assertLess(analysis["notes"][5]["quantization_margin_cents"], 15)
        self.assertEqual(
            ir["note_events"][5]["quantization_margin_cents"],
            analysis["notes"][5]["quantization_margin_cents"],
        )
        self.assertEqual(ir["phrase_boundaries"], [5.64])
        for source, output in zip(analysis["notes"], parsed["note_events"]):
            self.assertLess(abs(source["start"] - output["start"]), 0.002)
            self.assertLess(abs(source["duration"] - output["duration"]), 0.002)
        self.assertLess(abs(parsed["tempo_bpm"] - 102.56), 0.01)

    def test_contour_classifier_handles_both_directions(self):
        self.assertEqual(_classify_contour([44, 44, 52, 52, 54, 54, 52, 50, 46, 44]), "ascending_then_descending")
        self.assertEqual(_classify_contour([54, 52, 50, 48, 49, 52, 54]), "descending_then_ascending")

    def test_merges_short_same_pitch_boundary_fragment(self):
        notes = [
            {"pitch": 49, "start": 0.0, "duration": 0.7, "pitch_cents": 8.0, "confidence": 0.9},
            {"pitch": 48, "start": 0.71, "duration": 0.25, "pitch_cents": -4.0, "confidence": 0.8},
            {"pitch": 48, "start": 0.96, "duration": 0.55, "pitch_cents": 2.0, "confidence": 0.95},
        ]
        merged = _merge_same_pitch_fragments(notes)
        self.assertEqual([note["pitch"] for note in merged], [49, 48])
        self.assertAlmostEqual(merged[1]["duration"], 0.8, places=3)

    def test_high_register_fast_repeats_and_phrase_pause(self):
        sample_rate = 16_000
        beat = 0.38
        pitches = [60, 60, 67, 67, 69, 69, 67, 65, 65, 64, 64, 62, 62, 60]
        starts = [0.4 + i * beat for i in range(7)]
        second_phrase = starts[-1] + beat + 0.65
        starts.extend(second_phrase + i * beat for i in range(7))
        samples = np.zeros(round((starts[-1] + 0.5) * sample_rate), dtype=float)
        for pitch, start in zip(pitches, starts):
            duration = 0.30
            t = np.arange(round(duration * sample_rate)) / sample_rate
            frequency = 440 * 2 ** ((pitch - 69) / 12)
            vibrato = 2 ** ((0.25 * np.sin(2 * math.pi * 5.2 * t)) / 12)
            phase = 2 * math.pi * np.cumsum(frequency * vibrato) / sample_rate
            envelope = np.minimum(1, t / 0.025) * np.minimum(1, (duration - t) / 0.05)
            voice = 0.10 * envelope * (np.sin(phase) + 0.35 * np.sin(2 * phase) + 0.15 * np.sin(3 * phase))
            offset = round(start * sample_rate)
            samples[offset:offset + len(voice)] = voice
        samples += np.random.default_rng(22).normal(0, 0.003, len(samples))

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fast-humming.wav"
            with wave.open(str(path), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(sample_rate)
                audio.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
            result = extract_hummed_notes(path)

        self.assertEqual([note["pitch"] for note in result["notes"]], pitches)
        self.assertLess(max(abs(note["start"] - expected) for note, expected in zip(result["notes"], starts)), 0.02)
        self.assertLess(abs(result["tempo_bpm"] - 157.89), 1)
        self.assertLess(abs(result["phrase_boundaries"][0] - second_phrase), 0.02)

    def test_pitch_quantization_is_stable_around_half_semitone_boundary(self):
        sample_rate = 16_000
        starts = [0.5, 1.1, 1.7, 2.3]
        cents_above_midi_54 = [46, 46, 54, 54]
        duration = 0.42
        samples = np.zeros(sample_rate * 4, dtype=float)
        for start, cents in zip(starts, cents_above_midi_54):
            frequency = 440 * 2 ** ((54 - 69) / 12) * 2 ** (cents / 1200)
            t = np.arange(round(duration * sample_rate)) / sample_rate
            vibrato = 2 ** ((0.025 * np.sin(2 * math.pi * 5 * t)) / 12)
            phase = 2 * math.pi * np.cumsum(frequency * vibrato) / sample_rate
            envelope = np.minimum(1, t / 0.025) * np.minimum(1, (duration - t) / 0.05)
            voice = 0.13 * envelope * (
                np.sin(phase) + 0.35 * np.sin(2 * phase) + 0.15 * np.sin(3 * phase)
            )
            offset = round(start * sample_rate)
            samples[offset:offset + len(voice)] = voice
        samples += np.random.default_rng(33).normal(0, 0.001, len(samples))

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "near-semitone-boundary.wav"
            with wave.open(str(path), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(sample_rate)
                audio.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
            result = extract_hummed_notes(path)

        self.assertEqual([note["pitch"] for note in result["notes"]], [54, 54, 55, 55])
        self.assertTrue(all(note["quantization_margin_cents"] <= 6 for note in result["notes"]))
        self.assertLess(max(abs(note["start"] - expected) for note, expected in zip(result["notes"], starts)), 0.02)

    def test_legato_pitch_steps_split_without_silence(self):
        sample_rate = 16_000
        beat = 0.36
        pitches = [60, 62, 64, 67, 65, 64, 62, 60]
        samples = [0] * round(0.3 * sample_rate)
        phase = 0.0
        for pitch in pitches:
            frequency = 440 * 2 ** ((pitch - 69) / 12)
            for _ in range(round(beat * sample_rate)):
                phase += 2 * math.pi * frequency / sample_rate
                samples.append(round(9_000 * math.sin(phase)))
        samples.extend([0] * round(0.4 * sample_rate))

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legato.wav"
            with wave.open(str(path), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(sample_rate)
                audio.writeframes(array("h", samples).tobytes())
            result = extract_hummed_notes(path)

        self.assertEqual([note["pitch"] for note in result["notes"]], pitches)
        expected_starts = [0.3 + index * beat for index in range(len(pitches))]
        self.assertLess(max(abs(note["start"] - expected) for note, expected in zip(result["notes"], expected_starts)), 0.02)
        self.assertLess(abs(result["tempo_bpm"] - 166.67), 1)


if __name__ == "__main__":
    unittest.main()
