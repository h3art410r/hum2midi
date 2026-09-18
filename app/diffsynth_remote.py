"""HTTP client for the optional remote DiffSynth-Music Prosody worker."""

from __future__ import annotations

import os
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path


class DiffSynthRemoteError(RuntimeError):
    """Raised when the remote CUDA worker is unavailable or rejects a job."""


@dataclass(frozen=True)
class _Response:
    status: int
    data: bytes


@dataclass(frozen=True)
class DiffSynthRemoteConfig:
    url: str = "http://127.0.0.1:8765"
    timeout_seconds: int = 900
    token: str = ""

    @classmethod
    def from_env(cls) -> "DiffSynthRemoteConfig":
        return cls(
            url=os.getenv("DIFFSYNTH_REMOTE_URL", "http://127.0.0.1:8765").rstrip("/"),
            timeout_seconds=max(30, int(os.getenv("DIFFSYNTH_REMOTE_TIMEOUT_SECONDS", "900"))),
            token=os.getenv("DIFFSYNTH_REMOTE_TOKEN", ""),
        )


class DiffSynthRemoteClient:
    """Send normalized input audio to the Windows/CUDA worker."""

    def __init__(self, config: DiffSynthRemoteConfig | None = None):
        self.config = config or DiffSynthRemoteConfig.from_env()

    def status(self) -> str:
        return f"diffsynth-music-remote-prosody ({self.config.url})"

    def health(self) -> dict[str, object]:
        try:
            # Health must never inherit the long generation timeout; a frozen
            # CUDA worker should degrade quickly instead of making the public
            # page appear as a 502.
            probe = DiffSynthRemoteClient(
                DiffSynthRemoteConfig(self.config.url, timeout_seconds=min(5, self.config.timeout_seconds), token=self.config.token)
            )
            data = probe._request("GET", "/health")
            return data if isinstance(data, dict) else {"status": "ok"}
        except Exception as exc:
            return {"status": "unavailable", "error": str(exc)}

    def render(
        self,
        source: Path,
        style: str,
        output: Path,
        *,
        output_seconds: float | None = None,
        prompt: str | None = None,
        init_noise_level: float | None = None,
        seed: int | None = None,
    ) -> dict[str, object]:
        del style, init_noise_level
        if not source.is_file():
            raise DiffSynthRemoteError(f"Input audio not found: {source}")
        fields = {
            "prompt": prompt or "Create a compelling instrumental transformation of the input vocal prosody.",
            "duration": str(output_seconds or ""),
            "seed": str(seed if seed is not None else 101),
            "control": "prosody",
        }
        body, content_type = _multipart(fields, "audio", source.name, source.read_bytes(), "audio/wav")
        try:
            response = self._raw_request("POST", "/v1/generate", body, content_type)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[-1200:]
            raise DiffSynthRemoteError(f"DiffSynth worker HTTP {exc.code}: {detail}") from exc
        if response.status != 200:
            raise DiffSynthRemoteError(f"DiffSynth worker returned HTTP {response.status}")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(response.data)
        if not output.stat().st_size:
            raise DiffSynthRemoteError("DiffSynth worker returned an empty audio file")
        return {
            "provider": self.status(),
            "control": "prosody",
            "seconds": _wav_seconds(output),
            "requested_seconds": output_seconds,
            "bytes": output.stat().st_size,
        }

    def _request(self, method: str, path: str) -> object:
        response = self._raw_request(method, path, b"", "")
        import json
        return json.loads(response.data.decode("utf-8"))

    def _raw_request(self, method: str, path: str, body: bytes, content_type: str) -> _Response:
        headers = {"Accept": "application/json"}
        if content_type:
            headers["Content-Type"] = content_type
        if self.config.token:
            headers["Authorization"] = f"Bearer {self.config.token}"
        request = urllib.request.Request(self.config.url + path, data=body or None, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
                return _Response(response.status, response.read())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[-1200:]
            raise DiffSynthRemoteError(f"DiffSynth worker HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise DiffSynthRemoteError(f"Cannot reach DiffSynth worker {self.config.url}: {exc.reason}") from exc


def _multipart(fields: dict[str, str], name: str, filename: str, data: bytes, mime: str) -> tuple[bytes, str]:
    boundary = f"----hum2midi-{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for key, value in fields.items():
        chunks += [f"--{boundary}\r\n".encode(), f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode(), value.encode(), b"\r\n"]
    chunks += [f"--{boundary}\r\n".encode(), f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode(), f"Content-Type: {mime}\r\n\r\n".encode(), data, b"\r\n", f"--{boundary}--\r\n".encode()]
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def _wav_seconds(path: Path) -> float | None:
    import wave
    try:
        with wave.open(str(path), "rb") as handle:
            return round(handle.getnframes() / handle.getframerate(), 3)
    except (OSError, wave.Error, ZeroDivisionError):
        return None
