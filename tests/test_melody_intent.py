import unittest

from app.melody_intent import apply_melody_intent, musical_intent_candidate


def notes(pitches):
    return [{"pitch": pitch, "start": index * 0.5, "duration": 0.4, "velocity": 88, "confidence": 0.9}
            for index, pitch in enumerate(pitches)]


class MelodyIntentTests(unittest.TestCase):
    def test_repeated_pair_phrase_gets_conservative_scale_candidate(self):
        raw = [44, 44, 52, 52, 54, 54, 52, 50, 49, 48, 47, 46, 45, 44]
        selected, diagnostic = apply_melody_intent(notes(raw))
        self.assertEqual(diagnostic["mode"], "major-scale-repeated-pair")
        self.assertEqual([item["pitch"] for item in selected],
                         [44, 44, 51, 51, 53, 53, 51, 49, 49, 48, 48, 46, 46, 44])
        self.assertEqual([item["start"] for item in selected], [item["start"] for item in notes(raw)])
        self.assertEqual([item["duration"] for item in selected], [item["duration"] for item in notes(raw)])

    def test_other_phrase_is_left_as_measured(self):
        raw = [51, 51, 54, 56, 56, 53, 52, 49, 46, 46, 49, 51, 51, 50, 49]
        selected, diagnostic = apply_melody_intent(notes(raw))
        self.assertEqual(diagnostic["mode"], "measured")
        self.assertEqual([item["pitch"] for item in selected], raw)

    def test_explicit_measured_mode_disables_hypothesis(self):
        raw = [44, 44, 52, 52, 54, 54, 52, 50, 49, 48, 47, 46, 45, 44]
        selected, diagnostic = apply_melody_intent(notes(raw), "measured")
        self.assertEqual(diagnostic["mode"], "measured")
        self.assertEqual([item["pitch"] for item in selected], raw)

    def test_malformed_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            apply_melody_intent(notes([44, 44]), "bad")


if __name__ == "__main__":
    unittest.main()
