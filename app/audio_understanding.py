"""Qwen Omni cloud audio understanding adapter."""

from __future__ import annotations

import base64
import json
import os
import re
import wave
from pathlib import Path
from typing import Any

import httpx


class AudioUnderstandingError(RuntimeError):
    pass


class QwenOmniClient:
    def __init__(self) -> None:
        self.api_key = os.getenv("DASHSCOPE_API_KEY", "").strip()
        self.workspace_id = os.getenv("DASHSCOPE_WORKSPACE_ID", "").strip()
        self.model = os.getenv("QWEN_OMNI_MODEL", "qwen3.5-omni-flash").strip()
        configured_base = os.getenv("DASHSCOPE_BASE_URL", "").strip().rstrip("/")
        if configured_base:
            self.endpoint = f"{configured_base}/chat/completions" if configured_base.endswith("/v1") else configured_base
        elif self.workspace_id:
            self.endpoint = (
                f"https://{self.workspace_id}.cn-beijing.maas.aliyuncs.com"
                "/compatible-mode/v1/chat/completions"
            )
        else:
            self.endpoint = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
        if not self.api_key:
            raise AudioUnderstandingError("请在服务端配置 DASHSCOPE_API_KEY。")

    async def analyze_pitch_contour(self, audio_path: Path) -> str:
        """Ask Omni for a coarse, independently checkable pitch-direction cue.

        Exact note boundaries and F0 are measured by deterministic DSP after
        this mandatory cloud analysis; the model is not asked to invent MIDI.
        """
        _validate_audio_wav(audio_path)
        encoded = base64.b64encode(audio_path.read_bytes()).decode("ascii")
        prompt = (
            "只判断这段哼唱主旋律的整体音高走向，忽略音量变化。请选择："
            "先逐渐升高后逐渐降低、先逐渐降低后逐渐升高、基本保持同一音高、无法判断。"
            "只回答一个选项，不猜曲名、歌词，不输出音符或 MIDI。"
        )
        payload = {
            "model": self.model,
            "stream": True,
            "max_tokens": 64,
            "modalities": ["text"],
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "input_audio", "input_audio": {
                        "data": f"data:audio/wav;base64,{encoded}", "format": "wav",
                    }},
                    {"type": "text", "text": prompt},
                ],
            }],
        }
        chunks: list[str] = []
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(120, connect=15)) as client:
                async with client.stream(
                    "POST", self.endpoint,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=payload,
                ) as response:
                    if response.is_error:
                        body = (await response.aread()).decode("utf-8", "replace")[:800]
                        raise AudioUnderstandingError(f"Qwen Omni API {response.status_code}: {body}")
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
                        for choice in event.get("choices", []):
                            value = choice.get("delta", {}).get("content")
                            if isinstance(value, str):
                                chunks.append(value)
        except httpx.HTTPError as exc:
            raise AudioUnderstandingError(f"连接 Qwen Omni 失败：{exc}") from exc

        answer = "".join(chunks).strip()
        if "先逐渐升高后逐渐降低" in answer or "先升后降" in answer:
            return "ascending_then_descending"
        if "先逐渐降低后逐渐升高" in answer or "先降后升" in answer:
            return "descending_then_ascending"
        if "基本保持同一音高" in answer or "基本平" in answer:
            return "mostly_flat"
        raise AudioUnderstandingError("Qwen Omni 无法确认清晰的整体音高走向，请重录更清楚的哼唱。")

    async def extract_melody(self, audio_path: Path) -> dict[str, Any]:
        """Legacy direct-note probe retained for model evaluation only.

        The app pipeline uses ``analyze_pitch_contour`` plus deterministic
        signal measurements because direct Omni note JSON failed real-audio
        and calibration tests.
        """
        # This adapter advertises PCM WAV; reject mislabeled offline test inputs.
        _validate_audio_wav(audio_path)
        encoded = base64.b64encode(audio_path.read_bytes()).decode("ascii")
        prompt = (
            "任务：将随附录音中实际听到的单声部哼唱旋律转成 MIDI 音符事件。"
            "不要做曲名识别，不要假设歌名、歌词、标准曲谱或固定音符数；即使旋律熟悉，也不得凭记忆写音符。"
            "所有音高、时值和停顿必须来自这段录音。先完整听一遍确定各乐句和停顿，再逐个起音转录；"
            "每个重新起唱的音都是新的音符，即使与前一个音同音高也要拆开；延长且没有重新起唱的音保持为一个长音。"
            "start 是该音在原始录音中的起始秒数，duration 是该音实际持续秒数；保留真实的不等时值和静音，"
            "不要按固定间隔铺音符，也不要把整段压成重复循环。使用录音实际音高，不移调。"
            "忽略呼吸与噪声，不确定时降低 confidence；不得为补足旋律而臆造音符。"
            "提交前按时间顺序复听检查音高方向、重复起音、长音、停顿及结尾是否均与音频一致。"
            "严格输出一个 JSON 对象，不要 Markdown，格式："
            '{"tempo_bpm":100,"notes":'
            '[{"pitch":60,"start":0.0,"duration":0.4,"confidence":0.8}]}。'
            "pitch 是 MIDI 音高整数 36 到 84；start、duration 单位秒；按 start 排序；"
            "最多 80 个音符；没有清晰旋律时 notes 输出空数组。"
        )
        payload = {
            "model": self.model,
            "stream": True,
            "max_tokens": 2048,
            "modalities": ["text"],
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_audio",
                            "input_audio": {
                                "data": f"data:audio/wav;base64,{encoded}",
                                "format": "wav",
                            },
                        },
                        {
                            "type": "text",
                            "text": prompt,
                        },
                    ],
                }
            ],
        }
        content: list[str] = []
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(120, connect=15)) as client:
                async with client.stream(
                    "POST", self.endpoint,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=payload,
                ) as response:
                    if response.is_error:
                        body = (await response.aread()).decode("utf-8", "replace")[:800]
                        raise AudioUnderstandingError(f"Qwen Omni API {response.status_code}: {body}")
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
                        delta = event.get("choices", [{}])[0].get("delta", {})
                        text = delta.get("content")
                        if isinstance(text, str):
                            content.append(text)
        except httpx.HTTPError as exc:
            raise AudioUnderstandingError(f"连接 Qwen Omni 失败：{exc}") from exc

        response_text = "".join(content).strip()
        match = re.search(r"\{.*\}", response_text, re.S)
        if not match:
            raise AudioUnderstandingError("Qwen Omni 没有返回可解析的旋律 JSON。")
        try:
            result = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise AudioUnderstandingError("Qwen Omni 返回的旋律 JSON 格式无效。") from exc
        return _validate_notes(result)


def _validate_notes(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict) or not isinstance(result.get("notes"), list):
        raise AudioUnderstandingError("旋律结果缺少 notes 数组。")
    notes = []
    for item in result["notes"][:80]:
        try:
            pitch = int(item["pitch"])
            start = max(0.0, float(item["start"]))
            duration = float(item["duration"])
            confidence = float(item.get("confidence", 0.5))
        except (KeyError, TypeError, ValueError):
            continue
        if 36 <= pitch <= 84 and 0.04 <= duration <= 3.0 and start <= 15:
            notes.append({
                "pitch": pitch,
                "start": round(start, 3),
                "duration": round(duration, 3),
                "velocity": 88,
                "confidence": min(1.0, max(0.0, confidence)),
            })
    notes.sort(key=lambda note: note["start"])
    if not notes:
        raise AudioUnderstandingError("没有识别出清晰旋律，请换一段更清楚的哼唱重试。")
    try:
        tempo = float(result.get("tempo_bpm", 100))
    except (TypeError, ValueError):
        tempo = 100
    result_out = {"tempo_bpm": min(200, max(50, tempo)), "notes": notes}
    return result_out


def _validate_audio_wav(audio_path: Path) -> None:
    try:
        with wave.open(str(audio_path), "rb") as audio:
            if audio.getnchannels() != 1 or audio.getsampwidth() != 2 or audio.getframerate() != 16000:
                raise AudioUnderstandingError("模型输入需先转换为 16kHz 单声道 PCM16 WAV。")
    except (wave.Error, EOFError) as exc:
        raise AudioUnderstandingError("模型输入不是有效 PCM WAV，请先转换录音格式。") from exc

