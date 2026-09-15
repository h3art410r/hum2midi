import os
import unittest
from pathlib import Path
from unittest.mock import patch

from app.audio_renderer import renderer_status


class AudioRendererConfigTests(unittest.TestCase):
    def test_procedural_mode_is_explicit(self):
        with patch.dict(os.environ, {"AUDIO_RENDERER": "procedural"}, clear=False):
            self.assertEqual(renderer_status(), "procedural-midi-synth")

    def test_auto_detects_downloaded_soundfont_pair(self):
        with patch.dict(os.environ, {"AUDIO_RENDERER": "auto"}, clear=False):
            with patch("app.audio_renderer.find_fluidsynth", return_value=Path("fluidsynth.exe")):
                with patch("app.audio_renderer.find_soundfont", return_value=Path("GeneralUser-GS.sf2")):
                    self.assertEqual(renderer_status(), "fluidsynth-soundfont")


if __name__ == "__main__":
    unittest.main()
