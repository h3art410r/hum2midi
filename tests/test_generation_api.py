import asyncio
import unittest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch
from types import SimpleNamespace
from uuid import uuid4

import httpx

from app.main import BACKEND_LOGS, JOBS, app
from app.stable_audio import PROMPT_PLANS


class GenerationApiTests(unittest.TestCase):
    def test_funk_plan_reaches_generator_and_is_snapshotted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'input.wav'
            source.write_bytes(b'test')
            with patch('app.main.DATA', root), patch('app.main._to_wav', return_value=source), patch('app.main._make_provider') as factory, patch('app.main.StableAudioClient.audio_seconds', return_value=10.0):
                provider = MagicMock()
                factory.return_value = provider
                provider.status.return_value = 'test-provider'
                provider.config = SimpleNamespace(output_seconds=10.0)
                provider.render.return_value = {'seconds': 10, 'bytes': 44}

                async def create():
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                        response = await client.post('/api/generations', content=b'recording', headers={'X-Style-Preset': 'left-turn', 'X-Audio-Filename': 'hum.wav'})
                        self.assertEqual(response.status_code, 202)
                        job_id = response.json()['id']
                        for _ in range(100):
                            if JOBS[job_id]['status'] in ('completed', 'failed'):
                                break
                            await asyncio.sleep(.01)
                        return job_id

                job_id = asyncio.run(create())
                try:
                    self.assertEqual(JOBS[job_id]['status'], 'completed')
                    expected = [plan["prompt"] for plan in PROMPT_PLANS.values()]
                    self.assertEqual([call.kwargs['prompt'] for call in provider.render.call_args_list], expected)
                    self.assertEqual(len(JOBS[job_id]['variants']), 1)
                    self.assertEqual(JOBS[job_id]['prompts']['1'], PROMPT_PLANS['1']['prompt'])
                    self.assertEqual(JOBS[job_id]['prompt_translations']['1'], PROMPT_PLANS['1']['translation'])
                    self.assertEqual([call.kwargs['init_noise_level'] for call in provider.render.call_args_list], [plan['noise'] for plan in PROMPT_PLANS.values()])
                    self.assertTrue((root / job_id / 'prompt_snapshot.json').is_file())
                finally:
                    JOBS.pop(job_id, None)

    def test_prompt_presets_expose_funk_choice(self):
        async def get_presets():
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.get("/api/prompt-presets")

        response = asyncio.run(get_presets())
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body["plans"]), 1)
        self.assertEqual([item["name"] for item in body["plans"]], ["Funk"])
        self.assertEqual([item["noise"] for item in body["plans"]], [plan["noise"] for plan in PROMPT_PLANS.values()])
        for item in body["plans"]:
            self.assertTrue(item["prompt"])
            self.assertTrue(item["translation"])

    def test_debug_logs_endpoint_returns_backend_events(self):
        BACKEND_LOGS.clear()
        async def get_logs():
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.get("/api/debug/logs?since=0")

        response = asyncio.run(get_logs())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["logs"], [])

    def test_status_exposes_stable_audio_variants(self):
        job_id = uuid4().hex
        JOBS[job_id] = {
            "id": job_id,
            "status": "completed",
            "message": "Stable Audio variants ready",
            "provider": "stable-audio-3-local-sm-music",
            "variants": {
                "1": {"plan": "1", "plan_name": "1", "status": "completed", "seconds": 20.0, "bytes": 123},
            },
            "error": None,
            "source_path": "data/demo/source.wav",
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
        self.assertEqual(body["provider"], "stable-audio-3-local-sm-music")
        self.assertNotIn("source_path", body)
        self.assertEqual(body["variants"]["1"]["audio_url"], f"/api/generations/{job_id}/audio/1")


if __name__ == "__main__":
    unittest.main()
