"""Small HTTP client for the dedicated DiffSynth-Music Worker.

The clean backend intentionally knows only this protocol. It has no import
dependency on the legacy app or on the GPU runtime.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path


class WorkerError(RuntimeError):
    """The native Worker was unavailable or rejected a request."""


@dataclass(frozen=True)
class WorkerConfig:
    url: str = "http://127.0.0.1:8765"
    timeout_seconds: int = 900
    token: str = ""

    @classmethod
    def from_env(cls) -> "WorkerConfig":
        return cls(
            url=os.getenv("NATIVE_WORKER_URL", "http://127.0.0.1:8765").rstrip("/"),
            timeout_seconds=max(30, int(os.getenv("NATIVE_WORKER_TIMEOUT_SECONDS", "900"))),
            token=os.getenv("NATIVE_WORKER_TOKEN", ""),
        )


class NativeWorkerClient:
    def __init__(self, config: WorkerConfig | None = None):
        self.config = config or WorkerConfig.from_env()

    def health(self) -> dict[str, object]:
        try:
            return self._json_request("GET", "/health", timeout=min(5, self.config.timeout_seconds))
        except Exception as exc:
            return {"status": "unavailable", "error": str(exc)}

    def generate(
        self,
        source: Path,
        output: Path,
        *,
        prompt: str,
        seed: int,
        cfg_scale: float,
        steps: int,
        control: str = "prosody_control",
    ) -> dict[str, object]:
        if not source.is_file():
            raise WorkerError(f"Input audio not found: {source}")
        fields = {
            "prompt": prompt,
            "seed": str(seed),
            "cfg_scale": str(cfg_scale),
            "steps": str(steps),
            "control": control,
        }
        body, content_type = _multipart(
            fields,
            "audio",
            source.name,
            source.read_bytes(),
            "audio/wav",
        )
        started = time.perf_counter()
        try:
            response = self._raw_request("POST", "/v1/generate", body, content_type)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[-1600:]
            raise WorkerError(f"Worker HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise WorkerError(f"Cannot reach Worker {self.config.url}: {exc.reason}") from exc
        if response.status != 200:
            raise WorkerError(f"Worker returned HTTP {response.status}")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(response.data)
        if not output.stat().st_size:
            raise WorkerError("Worker returned an empty audio file")
        headers = {key.lower(): value for key, value in response.headers.items()}
        worker_request_id = headers.get("x-diffsynth-request-id", "")
        logs: list[dict[str, object]] = []
        if worker_request_id:
            try:
                payload = self._json_request(
                    "GET",
                    f"/debug/logs?request_id={urllib.parse.quote(worker_request_id, safe='')}&limit=300",
                    timeout=min(10, self.config.timeout_seconds),
                )
                if isinstance(payload.get("logs"), list):
                    logs = payload["logs"]
            except Exception:
                logs = []
        diagnostics: dict[str, object] = {
            "worker_url": self.config.url,
            "worker_request_id": worker_request_id,
            "worker_request_seconds": round(time.perf_counter() - started, 3),
            "worker_logs": logs,
        }
        for key in (
            "x-diffsynth-conditioning-seconds",
            "x-diffsynth-inference-seconds",
            "x-diffsynth-save-seconds",
            "x-diffsynth-total-seconds",
            "x-diffsynth-peak-allocated-gb",
            "x-diffsynth-peak-reserved-gb",
            "x-diffsynth-model-version",
            "x-diffsynth-negative-prompt-source",
            "x-diffsynth-control",
        ):
            if headers.get(key):
                diagnostics[key.removeprefix("x-diffsynth-").replace("-", "_")] = headers[key]
        return diagnostics

    def _json_request(self, method: str, path: str, *, timeout: int) -> dict[str, object]:
        response = self._raw_request(method, path, b"", "", timeout=timeout)
        try:
            payload = json.loads(response.data.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise WorkerError(f"Worker returned invalid JSON for {path}") from exc
        if not isinstance(payload, dict):
            raise WorkerError(f"Worker returned non-object JSON for {path}")
        return payload

    def _raw_request(
        self,
        method: str,
        path: str,
        body: bytes,
        content_type: str,
        *,
        timeout: int | None = None,
    ) -> "_Response":
        headers = {"Accept": "application/json"}
        if content_type:
            headers["Content-Type"] = content_type
        if self.config.token:
            headers["Authorization"] = f"Bearer {self.config.token}"
        request = urllib.request.Request(
            self.config.url + path,
            data=body or None,
            headers=headers,
            method=method,
        )
        with urllib.request.urlopen(request, timeout=timeout or self.config.timeout_seconds) as response:
            return _Response(response.status, response.read(), dict(response.headers.items()))


@dataclass(frozen=True)
class _Response:
    status: int
    data: bytes
    headers: dict[str, str]


def _multipart(fields: dict[str, str], name: str, filename: str, data: bytes, mime: str) -> tuple[bytes, str]:
    boundary = f"----hum2midi-native-{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for key, value in fields.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode(),
                value.encode(),
                b"\r\n",
            ]
        )
    chunks.extend(
        [
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode(),
            f"Content-Type: {mime}\r\n\r\n".encode(),
            data,
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
    )
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"
