"""A/B probe for whole-melody matching with a multimodal model.

Unlike the single-note probe, every candidate keeps the source rhythm and
uses a pitch-shifted copy of the singer's own note clips. This asks the model
to compare melodic shape over a phrase, without giving it a song title or
putting a reference score in the prompt.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import os
import random
import sys
import wave
from pathlib import Path

import httpx
import numpy as np
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.pitch_tracking import extract_hummed_notes


RATE = 16_000


def wav_bytes(samples: np.ndarray) -> bytes:
    stream = io.BytesIO()
    with wave.open(stream, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
    return stream.getvalue()


def shift_clip(clip: np.ndarray, semitones: float) -> np.ndarray:
    if abs(semitones) < 0.01:
        return clip.copy()
    ratio = 2 ** (semitones / 12)
    new_len = max(2, int(round(len(clip) / ratio)))
    resampled = np.interp(
        np.linspace(0, len(clip) - 1, new_len), np.arange(len(clip)), clip,
    )
    return np.interp(
        np.linspace(0, new_len - 1, len(clip)), np.arange(new_len), resampled,
    )


def candidate_audio(samples: np.ndarray, notes: list[dict[str, object]], pitches: list[int]) -> np.ndarray:
    output = np.zeros_like(samples, dtype=np.float64)
    for note, target in zip(notes, pitches):
        start = max(0, int(float(note["start"]) * RATE))
        end = min(len(samples), int((float(note["start"]) + float(note["duration"])) * RATE))
        clip = samples[start:end]
        if len(clip) < RATE // 10:
            continue
        shifted = shift_clip(clip, int(target) - int(note["pitch"]))
        output[start:start + len(shifted)] = shifted[: max(0, len(output) - start)]
    return output


def build_bundle(source: np.ndarray, notes: list[dict[str, object]], candidates: list[tuple[str, list[int]]]) -> bytes:
    chunks: list[np.ndarray] = [source, np.zeros(int(0.35 * RATE))]
    for _, pitches in candidates:
        chunks.append(candidate_audio(source, notes, pitches))
        chunks.append(np.zeros(int(0.35 * RATE)))
    return wav_bytes(np.concatenate(chunks))


async def ask(client: httpx.AsyncClient, endpoint: str, key: str, model: str, audio: bytes) -> str:
    prompt = (
        "第一段是原始人声哼唱，后面依次是候选 A、B、C。候选与原始录音使用相同的节奏和时长，"
        "只比较旋律的音高走向和每个音的相对关系，忽略音色、音量、候选顺序和任何熟悉的歌曲记忆。"
        "选择与第一段实际哼唱最一致的候选，只回答 A、B 或 C，不要猜歌名，不要解释。"
    )
    encoded = base64.b64encode(audio).decode("ascii")
    payload = {
        "model": model,
        "stream": False,
        "max_tokens": 16,
        "modalities": ["text"],
        "messages": [{"role": "user", "content": [
            {"type": "input_audio", "input_audio": {"data": f"data:audio/wav;base64,{encoded}", "format": "wav"}},
            {"type": "text", "text": prompt},
        ]}],
    }
    response = await client.post(endpoint, headers={"Authorization": f"Bearer {key}"}, json=payload)
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"].strip()


def motif_candidate(pitches: list[int]) -> list[int]:
    """Repeat the first phrase's interval pattern at the second phrase root."""
    if len(pitches) < 8:
        return pitches[:]
    first = pitches[:7]
    intervals = [value - first[0] for value in first]
    second_root = pitches[7]
    return first + [second_root + interval for interval in intervals]


async def main(args: argparse.Namespace) -> None:
    load_dotenv(args.env)
    key = os.getenv("DASHSCOPE_API_KEY", "").strip()
    workspace = os.getenv("DASHSCOPE_WORKSPACE_ID", "").strip()
    endpoint = f"https://{workspace}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/chat/completions"
    model = os.getenv("QWEN_OMNI_MODEL", "qwen3.5-omni-flash")
    with wave.open(str(args.audio), "rb") as source_file:
        source = np.frombuffer(source_file.readframes(source_file.getnframes()), dtype="<i2").astype(np.float64) / 32768
    notes = extract_hummed_notes(args.audio)["notes"]
    measured = [int(note["pitch"]) for note in notes]
    motif = motif_candidate(measured)
    # A deliberately different, smoothed contour is a negative control. None
    # of these candidates uses a song title or a reference score.
    smoothed = measured[:]
    for index in range(1, len(smoothed) - 1):
        smoothed[index] = int(round((smoothed[index - 1] + smoothed[index] + smoothed[index + 1]) / 3))
    canonical = [("dsp", measured), ("motif", motif), ("smoothed", smoothed)]
    rng = random.Random(args.seed)
    rows = []
    async with httpx.AsyncClient(timeout=120) as client:
        for repeat in range(max(1, args.repeats)):
            order = canonical[:]
            rng.shuffle(order)
            answer = await ask(client, endpoint, key, model, build_bundle(source, notes, order))
            index = {"A": 0, "B": 1, "C": 2}.get(answer[:1].upper())
            selected = order[index][0] if index is not None and index < len(order) else None
            rows.append({"repeat": repeat + 1, "order": [name for name, _ in order], "answer": answer, "selected": selected})
    print(json.dumps({"measured": measured, "motif": motif, "rows": rows}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("audio", type=Path)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--seed", type=int, default=11)
    parser.add_argument("--env", type=Path, default=Path(".env"))
    asyncio.run(main(parser.parse_args()))
