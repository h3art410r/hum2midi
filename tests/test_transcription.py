import os
import io
import unittest
from unittest.mock import patch

import mido

from app.transcription import (
    CloudTranscriptionError,
    DspTranscriptionEngine,
    KlangioTranscriptionEngine,
    TencentTranscriptionEngine,
    _midi_to_analysis,
    resolve_transcription_engine,
)


class TranscriptionBoundaryTests(unittest.TestCase):
    def test_dsp_is_explicit_default(self):
        with patch.dict(os.environ, {"TRANSCRIPTION_ENGINE": "dsp", "KLANGIO_API_KEY": ""}, clear=False):
            engine = resolve_transcription_engine()
        self.assertIsInstance(engine, DspTranscriptionEngine)
        self.assertEqual(engine.name, "dsp-yin")

    def test_auto_selects_cloud_only_when_key_exists(self):
        with patch.dict(os.environ, {"TRANSCRIPTION_ENGINE": "auto", "KLANGIO_API_KEY": "test-key"}, clear=False):
            engine = resolve_transcription_engine()
        self.assertIsInstance(engine, KlangioTranscriptionEngine)

    def test_explicit_cloud_without_key_is_visible_error(self):
        with patch.dict(os.environ, {"TRANSCRIPTION_ENGINE": "klangio", "KLANGIO_API_KEY": ""}, clear=False):
            with self.assertRaises(CloudTranscriptionError):
                resolve_transcription_engine()

    def test_auto_prefers_tencent_when_both_cloud_credentials_exist(self):
        with patch.dict(os.environ, {
            "TRANSCRIPTION_ENGINE": "auto",
            "TENCENT_SECRET_ID": "id",
            "TENCENT_SECRET_KEY": "key",
            "KLANGIO_API_KEY": "test-key",
        }, clear=False):
            engine = resolve_transcription_engine()
        self.assertIsInstance(engine, TencentTranscriptionEngine)

    def test_unknown_engine_does_not_silently_fallback(self):
        with patch.dict(os.environ, {"TRANSCRIPTION_ENGINE": "made-up", "KLANGIO_API_KEY": ""}, clear=False):
            with self.assertRaises(CloudTranscriptionError):
                resolve_transcription_engine()

    def test_provider_midi_parser_returns_monophonic_analysis(self):
        midi = mido.MidiFile(ticks_per_beat=480)
        track = mido.MidiTrack()
        midi.tracks.append(track)
        track.append(mido.MetaMessage("set_tempo", tempo=600000, time=0))
        track.append(mido.Message("note_on", note=60, velocity=80, time=0))
        track.append(mido.Message("note_off", note=60, velocity=0, time=480))
        track.append(mido.Message("note_on", note=62, velocity=80, time=0))
        track.append(mido.Message("note_off", note=62, velocity=0, time=480))
        stream = io.BytesIO()
        midi.save(file=stream)
        result = _midi_to_analysis(stream.getvalue())
        self.assertEqual([note["pitch"] for note in result["notes"]], [60, 62])
        self.assertAlmostEqual(result["tempo_bpm"], 100.0)
        self.assertEqual(result["source"], "klangio-vocal-cloud")


if __name__ == "__main__":
    unittest.main()
