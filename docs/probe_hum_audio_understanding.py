"""Ask Qwen Omni to describe a real humming clip without requesting MIDI."""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import sys
import wave
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.audio_understanding import QwenOmniClient

PROMPT = (
    "请用几句话描述这段音频里实际听到的内容，包括主要声音类型、"
    "音乐旋律大致的高低变化、节奏和停顿。不要猜曲名或歌词；"
    "听不确定的地方请直接说明。"
)
COUNT_PROMPT = (
    "请聆听这段哼唱并做粗略结构分析：1）估计有多少个独立音符，同音重复也分别计数；"
    "2）估计有几个乐句或段落，停顿大约在哪里；3）描述拍感，并估计速度 BPM。"
    "请简短回答并标出不确定项；不要猜曲名或歌词，也不要生成 MIDI。"
)
CONTOUR_PROMPT = (
    "请只根据这段录音本身，尽量具体地描述哼唱旋律：从开头到结尾按顺序列出你听到的每个明显音符或音高变化，"
    "用‘升高、降低、重复/持平’描述相邻音之间的关系，并粗略标注每个变化发生的时间；"
    "指出哪些位置像是重复音、哪些位置有停顿。不要猜曲名或歌词，不要输出 MIDI、音名或乐谱。"
    "如果无法可靠分辨全部音符，请明确说出能确定的范围和不确定之处，不要补齐猜测。"
)
JIANGPU_PROMPT = (
    "请把这段哼唱中最明显的单声部主旋律尽量记成简谱，严格只依据录音，不猜曲名或歌词。"
    "先说明你选定的调式主音（若无法判断绝对调性，就把开头稳定音暂记为 1，并明确这是相对简谱）；"
    "然后按顺序写出数字简谱 1-7，重复音要重复写，较高八度在数字右上方标点、较低八度在数字下方标点，"
    "用短横线表示延长，用竖线分乐句，休止处标 0。最后只简短列出最不确定的部分。"
    "不要输出 MIDI、音名、曲名或歌词；听不清的音请写问号，不要为了凑成完整旋律而猜。"
)
PITCH_COMPARE_PROMPT = (
    "请只比较下面指定片段里持续的人声哼唱基频高低，忽略响度、音色和伴奏；不要转写旋律。"
    "每题只回答 A 更高、B 更高、或无法判断，并用一句话说明依据："
    "1）A=1.1–1.7秒，B=2.7–3.2秒；"
    "2）A=3.7–4.2秒，B=6.2–6.7秒；"
    "3）A=2.7–3.2秒，B=8.2–8.7秒。"
    "不要猜曲名、歌词、音名或 MIDI。若片段没有清楚的持续音，请回答无法判断。"
)
PITCH_CONTOUR_CHOICE_PROMPT = (
    "只判断这段哼唱主旋律的整体音高走向，忽略音量变化。请选择：先逐渐升高后逐渐降低、"
    "先逐渐降低后逐渐升高、基本保持同一音高、无法判断。只回答一个选项，"
    "不猜曲名、歌词，不输出音符或 MIDI。"
)
NOTE_JSON_PROMPT = (
    "请把这段录音中实际听到的单声部哼唱按时间顺序转成音符事件。"
    "只记录录音里真实重新起唱的音符；同音重唱分别记录，持续长音不要拆开。"
    "pitch 使用 MIDI 音高整数，start 和 duration 使用本片段秒数。不要猜曲名、歌词或熟悉旋律，"
    "不要为了补成完整乐句而添加录音里听不清的音；无法确定的音跳过。"
    "严格只返回 JSON：{\"notes\":[{\"pitch\":60,\"start\":0.0,\"duration\":0.4}]}"
)


async def run(audio_path: Path, model: str, output_path: Path, prompt: str) -> dict:
    load_dotenv(ROOT / ".env")
    client = QwenOmniClient()
    data = audio_path.read_bytes()
    with wave.open(str(audio_path), "rb") as audio:
        audio_info = {
            "channels": audio.getnchannels(),
            "sample_width_bytes": audio.getsampwidth(),
            "sample_rate": audio.getframerate(),
            "duration_seconds": round(audio.getnframes() / audio.getframerate(), 3),
        }

    payload = {
        "model": model,
        "stream": True,
        "stream_options": {"include_usage": True},
        "max_tokens": 1024,
        "modalities": ["text"],
        "messages": [{
            "role": "user",
            "content": [
                {
                    "type": "input_audio",
                    "input_audio": {
                        "data": "data:audio/wav;base64," + base64.b64encode(data).decode("ascii"),
                        "format": "wav",
                    },
                },
                {"type": "text", "text": prompt},
            ],
        }],
    }
    chunks: list[str] = []
    record = {"model_requested": model, "audio": audio_info, "prompt": prompt}

    async with httpx.AsyncClient(timeout=httpx.Timeout(120, connect=15)) as session:
        async with session.stream(
            "POST", client.endpoint,
            headers={"Authorization": f"Bearer {client.api_key}"},
            json=payload,
        ) as response:
            record["http_status"] = response.status_code
            if response.is_error:
                record["error"] = (await response.aread()).decode("utf-8", "replace")[:1000]
            else:
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if raw == "[DONE]":
                        break
                    try:
                        event = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    for key in ("id", "model", "usage"):
                        if event.get(key) is not None:
                            record[key] = event[key]
                    for choice in event.get("choices", []):
                        if choice.get("finish_reason"):
                            record["finish_reason"] = choice["finish_reason"]
                        content = choice.get("delta", {}).get("content")
                        if isinstance(content, str):
                            chunks.append(content)

    record["response"] = "".join(chunks).strip()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(record, ensure_ascii=False, indent=2))
    return record


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("audio", type=Path, nargs="?", default=ROOT / "data/demo/model-compare/audio_16k.wav")
    parser.add_argument("--model", default="qwen3.5-omni-plus")
    parser.add_argument("--out", type=Path, default=ROOT / "data/demo/qwen-humming-probe/response.json")
    parser.add_argument("--count-structure", action="store_true", help="Ask for estimated note/phrase counts and tempo")
    parser.add_argument("--contour-detail", action="store_true", help="Ask for a time-ordered verbal pitch contour")
    parser.add_argument("--jianpu", action="store_true", help="Ask for a relative numbered musical notation transcription")
    parser.add_argument("--pitch-compare", action="store_true", help="Ask only pairwise high/low pitch comparisons")
    parser.add_argument("--pitch-contour-choice", action="store_true", help="Classify the overall high/low pitch contour")
    parser.add_argument("--note-json", action="store_true", help="Ask for audio-grounded note events as JSON")
    args = parser.parse_args()
    prompt = NOTE_JSON_PROMPT if args.note_json else PITCH_CONTOUR_CHOICE_PROMPT if args.pitch_contour_choice else PITCH_COMPARE_PROMPT if args.pitch_compare else JIANGPU_PROMPT if args.jianpu else CONTOUR_PROMPT if args.contour_detail else COUNT_PROMPT if args.count_structure else PROMPT
    asyncio.run(run(args.audio, args.model, args.out, prompt))


if __name__ == "__main__":
    main()
