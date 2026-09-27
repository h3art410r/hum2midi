"""Explicit A/B: official Prosody melody condition plus Reference style condition.

The product path remains Prosody-only. This script is an isolated experiment
that uses the model's Reference adapter with a project-generated style sample.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from native.prompts import STYLE_PROMPTS
from native.worker_client import NativeWorkerClient, WorkerConfig


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--steps", type=int, default=50)
    parser.add_argument("--style", choices=("funk", "lofi", "both"), default="both")
    parser.add_argument("--smoke", action="store_true", help="Use separate smoke-test filenames")
    args = parser.parse_args()

    folder = ROOT / "native/static/official"
    condition = folder / "experiment_onset_aligned_condition.wav"
    original_source = folder / "debug_recent_input.wav"
    style_ids = ("funk", "lofi") if args.style == "both" else (args.style,)
    client = NativeWorkerClient(WorkerConfig(
        url=os.getenv("NATIVE_WORKER_URL", "http://192.168.9.100:8765").rstrip("/"),
        timeout_seconds=900,
    ))
    health = client.health()
    if health.get("status") not in {"ok", "ready"} or health.get("worker_busy"):
        raise SystemExit(f"Worker unavailable or busy: {json.dumps(health, ensure_ascii=False)}")
    if "prosody_reference" not in health.get("supported_controls", []):
        raise SystemExit("Worker build does not support the explicit prosody_reference experiment yet")

    for style_id in style_ids:
        style = STYLE_PROMPTS[style_id]
        reference = folder / f"experiment_{style_id}_hook_preserving_prosody_cfg4.wav"
        if not reference.is_file():
            raise SystemExit(f"Missing style reference audio: {reference}")
        suffix = "_smoke" if args.smoke else ""
        output = folder / f"experiment_{style_id}_prosody_reference{suffix}.wav"
        print(f"START {style_id} Prosody+Reference steps={args.steps} reference={reference.name}", flush=True)
        diagnostics = client.generate(
            condition,
            output,
            prompt=style["prompt"],
            negative_prompt=style["negative_prompt"],
            lyrics="",
            seed=42,
            cfg_scale=4,
            steps=args.steps,
            control="prosody_reference",
            reference_audio=reference,
        )
        record = {
            "name": f"{style['name']} · Prosody + Reference 显式实验",
            "audio": output.name,
            "source_audio": original_source.name,
            "conditioning_audio": condition.name,
            "reference_audio": reference.name,
            "source_sha256": sha256(original_source),
            "conditioning_sha256": sha256(condition),
            "reference_sha256": sha256(reference),
            "seed": 42,
            "cfg_scale": 4,
            "steps": args.steps,
            "control": "prosody_reference",
            "template_models": [1, 2],
            "bpm": "model_default",
            "keyscale": "model_default",
            "timesignature": "model_default",
            "prompt": style["prompt"],
            "negative_prompt": style["negative_prompt"],
            "prompt_zh": style["translation"],
            "negative_zh": style["negative_translation"],
            "diagnostics": diagnostics,
            "status": "显式 A/B：Prosody 控制输入哼唱的音高/时序，Reference 使用本项目对应风格的既有生成音频作风格/音色参考；不使用 Control，不进入正式链路。",
        }
        output.with_suffix(".json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(
            f"DONE {style_id} seconds="
            f"{diagnostics.get('total_seconds', diagnostics.get('worker_request_seconds'))} "
            f"peak_reserved_gb={diagnostics.get('peak_reserved_gb', 'unknown')}",
            flush=True,
        )


if __name__ == "__main__":
    main()
