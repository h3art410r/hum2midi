"""Experimental two-audio Control+Prosody style-context test.

Prosody always comes from the user's original hum. Control comes from the
drums and bass of the best existing generated funk take, extracted through
the official ``pipe.extract_track`` path, and is supplied to both CFG branches.
This is an explicit A/B experiment; it does not change the product path.
"""

from __future__ import annotations

import json
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from native.worker_client import NativeWorkerClient, WorkerConfig


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cfg-scale", type=float)
    parser.add_argument("--suffix", default="both_branches")
    parser.add_argument("--control-tracks", default="drums,bass")
    args = parser.parse_args()
    folder = ROOT / "native/static/official"
    source = folder / "debug_recent_input.wav"
    control_source = folder / "experiment_funk_hook_recompose_seed7.wav"
    baseline_path = folder / "experiment_funk_hook_recompose_seed7.json"
    output = folder / f"experiment_funk_separate_style_control_{args.suffix}.wav"
    record_path = folder / f"experiment_funk_separate_style_control_{args.suffix}.json"

    for path in (source, control_source, baseline_path):
        if not path.is_file():
            raise SystemExit(f"Required A/B input is missing: {path}")

    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    prompt = baseline["prompt"]
    negative = baseline["negative_prompt"]
    seed = int(baseline["seed"])
    cfg_scale = float(args.cfg_scale if args.cfg_scale is not None else baseline["cfg_scale"])
    steps = int(baseline["steps"])
    control_tracks = tuple(
        item.strip().lower() for item in args.control_tracks.split(",") if item.strip()
    )
    if not control_tracks or any(item not in {"drums", "bass", "other"} for item in control_tracks):
        raise SystemExit("--control-tracks must be a comma-separated subset of drums,bass,other")

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
        f"tracks={','.join(control_tracks)} seed={seed} cfg={cfg_scale} steps={steps}",
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
        control_audio_tracks=control_tracks,
        control_audio_branches="both",
    )
    record = {
        "name": f"Funk · 原哼唱 Prosody + 官方{'+'.join(control_tracks)} Control 双分支 · CFG{cfg_scale:g}",
        "audio": output.name,
        "source_audio": source.name,
        "control_audio": control_source.name,
        "control_audio_tracks": list(control_tracks),
        "control_audio_branches": "both",
        "seed": seed,
        "cfg_scale": cfg_scale,
        "steps": steps,
        "control": "prosody_control",
        "control_branch_design": {
            "positive": f"Control {','.join(control_tracks)} extracted from prior Funk candidate + Prosody from original hum",
            "negative": f"the same Control {','.join(control_tracks)} and Prosody from original hum",
        },
        "prompt": prompt,
        "negative_prompt": negative,
        "prompt_zh": (
            f"原哼唱只用于 Prosody 旋律条件；从 Funk 候选中按官方 extract_track 提取 {','.join(control_tracks)}，"
            "作为 Control 条件。正向和负向 CFG 分支使用相同的伴奏 Control 与原始 Prosody；"
            f"提示词、随机种子和步数与 seed7 基线相同；本次 CFG={cfg_scale:g}。"
        ),
        "status": (
            f"显式技术 A/B：原录音提供 Prosody，已有 Funk 候选仅提取 {','.join(control_tracks)} 作为双分支 Control；"
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
