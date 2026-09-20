"""HTTP client for the optional remote DiffSynth-Music Prosody worker."""

from __future__ import annotations

import os
import logging
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote


logger = logging.getLogger(__name__)


class DiffSynthRemoteError(RuntimeError):
    """Raised when the remote CUDA worker is unavailable or rejects a job."""


@dataclass(frozen=True)
class _Response:
    status: int
    data: bytes
    headers: dict[str, str]


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
        control_profile: str = "control_prosody",
        denoising_strength: float | None = None,
        cfg_scale: float | None = None,
        steps: int | None = None,
    ) -> dict[str, object]:
        del style, init_noise_level
        if not source.is_file():
            raise DiffSynthRemoteError(f"Input audio not found: {source}")
        fields = {
            "prompt": prompt or "Create a compelling instrumental transformation of the input vocal prosody.",
            "duration": str(output_seconds or ""),
            "seed": str(seed if seed is not None else 101),
            "control": "prosody",
            "control_profile": control_profile,
            "use_input_audio": str(denoising_strength is not None).lower(),
            "denoising_strength": "" if denoising_strength is None else str(denoising_strength),
            "cfg_scale": "" if cfg_scale is None else str(cfg_scale),
            "steps": "" if steps is None else str(steps),
        }
        body, content_type = _multipart(fields, "audio", source.name, source.read_bytes(), "audio/wav")
        request_started = time.perf_counter()
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
        request_seconds = round(time.perf_counter() - request_started, 3)
        worker_headers = {
            key: response.headers.get(key, "")
            for key in (
                "X-DiffSynth-Request-Id",
                "X-DiffSynth-Conditioning-Seconds",
                "X-DiffSynth-Inference-Seconds",
                "X-DiffSynth-Save-Seconds",
                "X-DiffSynth-Total-Seconds",
                "X-DiffSynth-Peak-Allocated-GB",
                "X-DiffSynth-Peak-Reserved-GB",
            )
            if response.headers.get(key)
        }
        worker_logs: list[dict[str, object]] = []
        worker_request_id = worker_headers.get("X-DiffSynth-Request-Id")
        if worker_request_id:
            try:
                logs_response = self._request(
                    "GET",
                    f"/debug/logs?request_id={quote(worker_request_id, safe='')}&limit=200",
                )
                if isinstance(logs_response, dict) and isinstance(logs_response.get("logs"), list):
                    worker_logs = logs_response["logs"]
            except Exception as exc:
                logger.warning("diffsynth_worker_logs_unavailable request_id=%s error=%s", worker_request_id, exc)
        logger.info(
            "diffsynth_remote_complete url=%s profile=%s cfg=%s steps=%s seed=%s denoise=%s "
            "request_seconds=%s response_bytes=%s output_bytes=%s worker=%s",
            self.config.url, control_profile, cfg_scale, steps, seed,
            denoising_strength, request_seconds, len(response.data), output.stat().st_size, worker_headers,
        )
        return {
            "provider": self.status(),
            "control": "control+prosody",
            "seconds": _wav_seconds(output),
            "requested_seconds": output_seconds,
            "bytes": output.stat().st_size,
            "request_seconds": request_seconds,
            "response_bytes": len(response.data),
            "requested_control_profile": control_profile,
            "requested_cfg_scale": cfg_scale,
            "requested_steps": steps,
            "requested_seed": seed,
            "requested_denoising_strength": denoising_strength,
            "worker_request_id": worker_headers.get("X-DiffSynth-Request-Id"),
            "worker_conditioning_seconds": _header_float(worker_headers, "X-DiffSynth-Conditioning-Seconds"),
            "worker_inference_seconds": _header_float(worker_headers, "X-DiffSynth-Inference-Seconds"),
            "worker_save_seconds": _header_float(worker_headers, "X-DiffSynth-Save-Seconds"),
            "worker_total_seconds": _header_float(worker_headers, "X-DiffSynth-Total-Seconds"),
            "worker_peak_allocated_gb": _header_float(worker_headers, "X-DiffSynth-Peak-Allocated-GB"),
            "worker_peak_reserved_gb": _header_float(worker_headers, "X-DiffSynth-Peak-Reserved-GB"),
            "worker_logs": worker_logs,
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
                return _Response(response.status, response.read(), dict(response.headers.items()))
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


def _header_float(headers: dict[str, str], name: str) -> float | None:
    value = headers.get(name, "")
    try:
        return round(float(value), 3) if value else None
    except ValueError:
        return None
