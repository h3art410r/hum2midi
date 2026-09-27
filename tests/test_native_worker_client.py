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


if __name__ == "__main__":
    unittest.main()
