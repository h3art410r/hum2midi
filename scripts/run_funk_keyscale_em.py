"""Test explicit official key metadata against DiffSynth's B-minor default."""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from native.worker_client import NativeWorkerClient, WorkerConfig
from run_funk_payoff_candidate import PROMPT, NEGATIVE

def main():
    source = ROOT / "native/static/official/debug_recent_input.wav"
    output = ROOT / "native/static/official/experiment_funk_keyscale_e_minor.wav"
    keyscale = "E minor"
    client = NativeWorkerClient(WorkerConfig(url="http://192.168.9.100:8765", timeout_seconds=900))
    diagnostics = client.generate(source, output, prompt=PROMPT,
        negative_prompt=NEGATIVE, lyrics="", seed=42, cfg_scale=4.5,
        steps=50, control="prosody_control", keyscale=keyscale)
    record = {
        "name": "Funk · 官方 keyscale=E minor 对照",
        "audio": output.name, "source_audio": source.name,
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "seed": 42, "cfg_scale": 4.5, "steps": 50,
        "control": "prosody_control", "bpm": "default 100",
        "keyscale": keyscale, "timesignature": "default 4",
        "prompt": PROMPT, "negative_prompt": NEGATIVE,
        "prompt_zh": "提示词和其它参数与 CFG4.5 基线相同。唯一变化是把官方模型默认的 B 小调改成从输入旋律音级分布估出的 E 小调，观察模型是否更容易围绕输入调性编配。调性估计有歧义，E 小调与 G 大调相对调均需试听验证。",
        "status": "单变量官方元数据 A/B；调性估计有歧义，不自动用于正式链路",
        "diagnostics": diagnostics,
    }
    folder = output.parent
    (folder / "experiment_funk_keyscale_e_minor.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest_path = folder / "debug_experiment.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["items"] = [record] + [item for item in manifest["items"] if item.get("audio") != output.name]
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"audio": output.name, "diagnostics": diagnostics}, ensure_ascii=False), flush=True)

if __name__ == "__main__":
    main()
