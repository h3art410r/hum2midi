"""Experimental two-audio Control+Prosody style-context test.

Prosody always comes from the user's original hum. Control comes from the
best existing generated funk take and is supplied only to the positive CFG
branch, as in the official Reference example's positive-only template use.
This is an explicit A/B experiment; it does not change the product path.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from native.worker_client import NativeWorkerClient, WorkerConfig


def main() -> None:
    folder = ROOT / "native/static/official"
    source = folder / "debug_recent_input.wav"
    control_source = folder / "experiment_funk_hook_recompose_seed7.wav"
    baseline_path = folder / "experiment_funk_hook_recompose_seed7.json"
    output = folder / "experiment_funk_separate_style_control.wav"
    record_path = folder / "experiment_funk_separate_style_control.json"

    for path in (source, control_source, baseline_path):
        if not path.is_file():
            raise SystemExit(f"Required A/B input is missing: {path}")

    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    prompt = baseline["prompt"]
    negative = baseline["negative_prompt"]
    seed = int(baseline["seed"])
    cfg_scale = float(baseline["cfg_scale"])
    steps = int(baseline["steps"])

    client = NativeWorkerClient(
        WorkerConfig(url="http://192.168.9.100:8765", timeout_seconds=900)
    )
    health = client.health()
    if health.get("status") != "ok" or health.get("worker_busy"):
        raise SystemExit(f"Worker is unavailable or busy: {json.dumps(health, ensure_ascii=False)}")
    if "prosody_control" not in health.get("supported_controls", []):
        raise SystemExit("Current worker does not expose the explicit Control+Prosody experiment")

    print(
        f"START source=original_hum control_context={control_source.name} "
        f"seed={seed} cfg={cfg_scale} steps={steps}",
        flush=True,
    )
    diagnostics = client.generate(
        source,
        output,
        control_audio=control_source,
        prompt=prompt,
        negative_prompt=negative,
        lyrics="",
        seed=seed,
        cfg_scale=cfg_scale,
        steps=steps,
        control="prosody_control",
    )
    record = {
        "name": "Funk · 原哼唱 Prosody + 已生成 Funk Control 风格上下文",
        "audio": output.name,
        "source_audio": source.name,
        "control_audio": control_source.name,
        "seed": seed,
        "cfg_scale": cfg_scale,
        "steps": steps,
        "control": "prosody_control",
        "control_branch_design": {
            "positive": "Control from prior Funk candidate + Prosody from original hum",
            "negative": "Prosody from original hum only",
        },
        "prompt": prompt,
        "negative_prompt": negative,
        "prompt_zh": baseline.get("prompt_zh", ""),
        "status": (
            "显式技术 A/B：原录音仅提供旋律/节奏 Prosody；已有 Funk 候选只提供正向 Control 风格上下文；"
            "未改正式 Prosody-only 链路。"
        ),
        "diagnostics": diagnostics,
        "worker_health": health,
    }
    record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest_path = folder / "debug_experiment.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["items"] = [record] + [
        item for item in manifest.get("items", []) if item.get("audio") != output.name
    ]
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"DONE total={diagnostics.get('total_seconds')}s peak={diagnostics.get('peak_reserved_gb')}GB", flush=True)


if __name__ == "__main__":
    main()
