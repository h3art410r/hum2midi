import asyncio
import unittest
from uuid import uuid4

import httpx

from app.main import JOBS, app


class GenerationApiTests(unittest.TestCase):
    def test_status_exposes_compact_melody_events_for_debug_view(self):
        job_id = uuid4().hex
        JOBS[job_id] = {
            "id": job_id,
            "status": "completed",
            "message": "done",
            "variants": {},
            "error": None,
            "pitch_trace": [{"time": 1.1, "pitch": 44.2}],
            "ir": {
                "tempo_bpm": 102.56,
                "phrase_boundaries": [5.64],
                "note_events": [
                    {"pitch": 44, "start": 1.04, "duration": 0.53, "velocity": 88, "confidence": 0.879, "quantization_margin_cents": 34.2},
                ],
                "source": "qwen-omni-contour+deterministic-yin",
            },
        }
        try:
            async def get_status():
                transport = httpx.ASGITransport(app=app)
                async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                    return await client.get(f"/api/generations/{job_id}")

            response = asyncio.run(get_status())
        finally:
            JOBS.pop(job_id, None)

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["melody"], {
            "tempo_bpm": 102.56,
            "phrase_boundaries": [5.64],
            "note_events": [{"pitch": 44, "start": 1.04, "duration": 0.53, "quantization_margin_cents": 34.2}],
            "pitch_trace": [{"time": 1.1, "pitch": 44.2}],
        })
        self.assertNotIn("ir", body)
        self.assertNotIn("pitch_trace", body)
        self.assertNotIn("source", body["melody"])


if __name__ == "__main__":
    unittest.main()
