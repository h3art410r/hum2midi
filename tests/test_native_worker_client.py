import unittest

from native.worker_client import _multipart


class WorkerMultipartTests(unittest.TestCase):
    def test_reference_audio_is_sent_as_a_second_file_part(self):
        body, content_type = _multipart(
            {"control": "prosody_reference", "prompt": "instrumental funk"},
            "audio",
            "hum.wav",
            b"hum-data",
            "audio/wav",
            reference=("reference_audio", "funk.wav", b"reference-data", "audio/wav"),
        )

        rendered = body.decode("utf-8")
        self.assertTrue(content_type.startswith("multipart/form-data; boundary="))
        self.assertIn('name="audio"; filename="hum.wav"', rendered)
        self.assertIn('name="reference_audio"; filename="funk.wav"', rendered)
        self.assertEqual(rendered.count("Content-Disposition: form-data; name="), 4)
        self.assertTrue(body.endswith(b"--\r\n"))

    def test_standard_request_does_not_gain_reference_file(self):
        body, _ = _multipart({"control": "prosody"}, "audio", "hum.wav", b"hum-data", "audio/wav")

        rendered = body.decode("utf-8")
        self.assertIn('name="audio"; filename="hum.wav"', rendered)
        self.assertNotIn("reference_audio", rendered)
        self.assertEqual(rendered.count("Content-Disposition: form-data; name="), 2)

    def test_separate_control_audio_is_sent_as_a_third_file_part(self):
        body, _ = _multipart(
            {
                "control": "prosody_control",
                "control_audio_tracks": "drums,bass",
                "control_audio_branches": "both",
            },
            "audio",
            "hum.wav",
            b"hum-data",
            "audio/wav",
            extra_control=("control_audio", "funk-context.wav", b"funk-data", "audio/wav"),
        )

        rendered = body.decode("utf-8")
        self.assertIn('name="audio"; filename="hum.wav"', rendered)
        self.assertIn('name="control_audio"; filename="funk-context.wav"', rendered)
        self.assertIn('name="control_audio_tracks"', rendered)
        self.assertIn("drums,bass", rendered)
        self.assertIn('name="control_audio_branches"', rendered)
        self.assertIn("both", rendered)
        self.assertEqual(rendered.count("filename="), 2)

    def test_secondary_control_source_is_sent_as_a_fourth_file_part(self):
        body, _ = _multipart(
            {
                "control": "prosody_control",
                "control_audio_tracks": "drums,bass",
                "control_audio_other_weight": "0.35",
            },
            "audio",
            "hum.wav",
            b"hum-data",
            "audio/wav",
            extra_control=("control_audio", "funk-drums.wav", b"drum-bass-data", "audio/wav"),
            extra_control_other=("control_audio_other", "melody-other.wav", b"other-data", "audio/wav"),
        )

        rendered = body.decode("utf-8")
        self.assertIn('name="control_audio"; filename="funk-drums.wav"', rendered)
        self.assertIn('name="control_audio_other"; filename="melody-other.wav"', rendered)
        self.assertIn('name="control_audio_other_weight"', rendered)
        self.assertIn("0.35", rendered)
        self.assertEqual(rendered.count("filename="), 3)


if __name__ == "__main__":
    unittest.main()
