import os
import unittest
from unittest.mock import patch

from app.melody_semantic import _candidate


def make_notes(pitches):
    return [{"pitch": pitch, "start": index * 0.5, "duration": 0.4, "pitch_cents": 12.0}
            for index, pitch in enumerate(pitches)]


class MelodySemanticTests(unittest.TestCase):
    def test_high_similarity_replaces_pitch_only(self):
        raw = [44, 44, 52, 52, 54, 54, 52, 50, 49, 48, 47, 46, 45, 44]
        original = make_notes(raw)
        replacement = [44, 44, 51, 51, 53, 53, 51, 49, 49, 48, 48, 46, 46, 44]
        with patch.dict(os.environ, {"QWEN_MELODY_MIN_SIMILARITY": "0.90"}, clear=False):
            selected, diagnostic = _candidate({
                "similar": True, "similarity": 0.96, "confidence": 0.95,
                "jianpu": [1, 1, 5, 5, 6, 6, 5, 4, 4, 3, 3, 2, 2, 1],
                "pitches": replacement,
            }, original)
        self.assertEqual(diagnostic["mode"], "semantic-memory")
        self.assertEqual([item["pitch"] for item in selected], replacement)
        self.assertEqual([item["start"] for item in selected], [item["start"] for item in original])
        self.assertEqual([item["duration"] for item in selected], [item["duration"] for item in original])
        self.assertTrue(all(item["pitch_cents"] == 0.0 for item in selected))

    def test_low_similarity_keeps_measured_sequence(self):
        raw = [44, 44, 52, 52, 54, 54, 52, 50, 49, 48, 47, 46, 45, 44]
        selected, diagnostic = _candidate({
            "similar": False, "similarity": 0.40, "confidence": 0.4, "pitches": [],
        }, make_notes(raw))
        self.assertEqual(diagnostic["mode"], "measured")
        self.assertEqual([item["pitch"] for item in selected], raw)

    def test_model_memory_key_is_transposed_to_measured_first_note(self):
        raw = [44, 44, 52, 52, 54, 54, 52, 50, 49, 48, 47, 46, 45, 44]
        selected, diagnostic = _candidate({
            "similar": True, "similarity": 0.96, "confidence": 0.95,
            "jianpu": [1, 1, 5, 5, 6, 6, 5, 4, 4, 3, 3, 2, 2, 1],
            # The model remembered the melody around C4.
            "pitches": [60, 60, 67, 67, 69, 69, 67, 65, 65, 64, 64, 62, 62, 60],
        }, make_notes(raw))
        self.assertEqual(diagnostic["mode"], "semantic-memory")
        self.assertEqual([item["pitch"] for item in selected],
                         [44, 44, 51, 51, 53, 53, 51, 49, 49, 48, 48, 46, 46, 44])

    def test_wrong_length_never_changes_notes(self):
        raw = [60, 62, 64]
        selected, diagnostic = _candidate({
            "similar": True, "similarity": 0.99, "confidence": 0.99, "pitches": [60, 62],
        }, make_notes(raw))
        self.assertEqual(diagnostic["mode"], "measured")
        self.assertEqual([item["pitch"] for item in selected], raw)


if __name__ == "__main__":
    unittest.main()
