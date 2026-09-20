"""Local Stable Audio 3 audio-to-audio provider.

The provider deliberately shells out to Stability AI's official TFLite CLI
instead of importing the model into the FastAPI process. This keeps the demo's
web server small, isolates the model environment, and makes CPU/GPU backends
swappable through one command boundary.
"""

from __future__ import annotations

import os
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path


class StableAudioError(RuntimeError):
    """Raised when the local Stable Audio runtime is unavailable or fails."""


PROMPT_PLANS = {
    "1": {
        "name": "Funk",
        "description": "保留原始旋律和节奏，转换成完整的器乐 Funk 编曲。",
        "noise": 0.40,
        "prompt": "Create a polished instrumental funk track from the input humming. Keep the original melody, phrasing, and rhythmic identity clearly recognizable. Replace the raw humming with a strong bass groove, syncopated drums, rhythmic guitar, tight keyboard accents, and a catchy musical arrangement. Make it energetic, stylish, and memorable. Do not keep the original vocal recording or its background noise.",
        "translation": "根据输入哼唱创作一首完整的器乐 Funk 音乐。清楚保留原始旋律、乐句和节奏身份，用有力度的贝斯律动、切分鼓点、节奏吉他、键盘点缀和抓耳编曲替换原始哼唱。整体要有能量、有风格、有记忆点，不保留原始人声或底噪。",
    },
    "2": {
        "name": "Lo-fi",
        "description": "保留原始旋律和节奏，转换成温暖耐听的器乐 Lo-fi 编曲。",
        "noise": 0.40,
        "prompt": "Create a polished instrumental lo-fi track from the input humming. Keep the original melody, phrasing, and rhythmic identity clearly recognizable. Replace the raw humming with warm chords, a relaxed but precise drum groove, mellow bass, dusty keys, subtle texture, and an emotionally memorable arrangement. Make it intimate, modern, and replayable. Do not keep the original vocal recording or its background noise.",
        "translation": "根据输入哼唱创作一首完整的器乐 Lo-fi 音乐。清楚保留原始旋律、乐句和节奏身份，用温暖和弦、松弛但准确的鼓组、柔和贝斯、带颗粒感的键盘和细微质感替换原始哼唱。整体要亲密、现代、耐听，不保留原始人声或底噪。",
    },
}

# Compatibility names for small scripts that imported the old mapping.
PROMPT_PRESETS = PROMPT_PLANS
PROMPT_TRANSLATIONS = {plan_id: {"transform": plan["translation"]} for plan_id, plan in PROMPT_PLANS.items()}
STYLE_PROMPTS = {
    "transform": PROMPT_PLANS["1"]["prompt"],
    "funk": PROMPT_PLANS["1"]["prompt"],
    "lofi": PROMPT_PLANS["2"]["prompt"],
}


@dataclass(frozen=True)
class StableAudioConfig:
    root: Path
    model: str = "sm-music"
    decoder: str = "same-s"
    steps: int = 8
    threads: int = 8
    init_noise_level: float = 0.70
    seed: int | None = 101
    output_seconds: float = 20.0
    timeout_seconds: int = 600

    @classmethod
    def from_env(cls) -> "StableAudioConfig":
        app_root = Path(__file__).resolve().parent.parent
        default_root = app_root.parent / "stable-audio-3-local" / "optimized" / "tflite"
        return cls(
            root=Path(os.getenv("STABLE_AUDIO_ROOT", str(default_root))).expanduser(),
            model=os.getenv("STABLE_AUDIO_MODEL", "sm-music"),
            decoder=os.getenv("STABLE_AUDIO_DECODER", "same-s"),
            steps=max(1, int(os.getenv("STABLE_AUDIO_STEPS", "8"))),
            threads=max(1, int(os.getenv("STABLE_AUDIO_THREADS", str(min(os.cpu_count() or 8, 8))))),
            init_noise_level=min(1.0, max(0.0, float(os.getenv("STABLE_AUDIO_INIT_NOISE_LEVEL", "0.70")))),
            seed=(int(os.getenv("STABLE_AUDIO_SEED", "101")) if os.getenv("STABLE_AUDIO_SEED", "101").strip() else None),
            output_seconds=float(os.getenv("STABLE_AUDIO_OUTPUT_SECONDS", "20")),
            timeout_seconds=max(30, int(os.getenv("STABLE_AUDIO_TIMEOUT_SECONDS", "600"))),
        )


class StableAudioClient:
    def __init__(self, config: StableAudioConfig | None = None):
        self.config = config or StableAudioConfig.from_env()

    @property
    def script(self) -> Path:
        suffix = ".bat" if os.name == "nt" else ""
        candidate = self.config.root / (f"sa3{suffix}")
        if candidate.is_file():
            return candidate
        # The repository also ships a PowerShell wrapper. Prefer the batch
        # entrypoint on Windows because it is the documented no-activation path.
        powershell = self.config.root / "sa3.ps1"
        if os.name == "nt" and powershell.is_file():
            return powershell
        return candidate

    def status(self) -> str:
        if self.script.is_file():
            return f"stable-audio-3-local-{self.config.model}"
        return "stable-audio-3-local-unavailable"

    def build_command(
        self,
        source: Path,
        style: str,
        output: Path,
        *,
        output_seconds: float | None = None,
        prompt: str | None = None,
        init_noise_level: float | None = None,
        seed: int | None = None,
    ) -> list[str]:
        if style not in STYLE_PROMPTS:
            raise StableAudioError(f"Unsupported Stable Audio style: {style}")
        if not self.script.is_file():
            raise StableAudioError(
                f"Stable Audio CLI not found: {self.script}. "
                "Set STABLE_AUDIO_ROOT to the optimized/tflite directory."
            )
        args = [
            "--prompt", prompt or STYLE_PROMPTS[style],
            "--dit", self.config.model,
            "--decoder", self.config.decoder,
            "--init-audio", str(source),
            "--init-noise-level", str(
                self.config.init_noise_level if init_noise_level is None else init_noise_level
            ),
            "--seconds", str(output_seconds if output_seconds is not None else self.config.output_seconds),
            "--steps", str(self.config.steps),
            "--threads", str(self.config.threads),
        ]
        effective_seed = self.config.seed if seed is None else seed
        if effective_seed is not None:
            args.extend(["--seed", str(effective_seed)])
        args.extend(["--out", str(output)])
        if self.script.suffix.lower() == ".bat":
            return ["cmd", "/d", "/c", str(self.script), *args]
        if self.script.suffix.lower() == ".ps1":
            return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(self.script), *args]
        return [str(self.script), *args]

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
        command = self.build_command(
            source,
            style,
            output,
            output_seconds=output_seconds,
            prompt=prompt,
            init_noise_level=init_noise_level,
            seed=seed,
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        try:
            result = subprocess.run(
                command,
                cwd=self.config.root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.config.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise StableAudioError(
                f"Stable Audio {style} generation exceeded {self.config.timeout_seconds}s"
            ) from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "process failed").strip()[-1200:]
            raise StableAudioError(f"Stable Audio {style} failed ({result.returncode}): {detail}")
        if not output.is_file() or output.stat().st_size == 0:
            detail = (result.stdout or result.stderr or "no output file").strip()[-1200:]
            raise StableAudioError(f"Stable Audio {style} returned no audio: {detail}")
        return {
            "provider": self.status(),
            "style": style,
            "seconds": self._wav_seconds(output),
            "requested_seconds": output_seconds if output_seconds is not None else self.config.output_seconds,
            "bytes": output.stat().st_size,
        }

    @staticmethod
    def audio_seconds(path: Path) -> float | None:
        return StableAudioClient._wav_seconds(path)

    @staticmethod
    def _wav_seconds(path: Path) -> float | None:
        try:
            with wave.open(str(path), "rb") as handle:
                return round(handle.getnframes() / handle.getframerate(), 3)
        except (OSError, wave.Error, ZeroDivisionError):
            return None
