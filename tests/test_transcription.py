import os
import unittest
from unittest.mock import patch

from app.transcription import (
    CloudTranscriptionError,
    DspTranscriptionEngine,
    KlangioTranscriptionEngine,
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

    def test_unknown_engine_does_not_silently_fallback(self):
        with patch.dict(os.environ, {"TRANSCRIPTION_ENGINE": "made-up", "KLANGIO_API_KEY": ""}, clear=False):
            with self.assertRaises(CloudTranscriptionError):
                resolve_transcription_engine()


if __name__ == "__main__":
    unittest.main()
