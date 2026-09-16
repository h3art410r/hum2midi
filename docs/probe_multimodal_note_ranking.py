"""A/B probe: can a multimodal model rank nearby pitches for one hummed note?

This is an experiment, not part of the production path. It tests a narrower
use of audio understanding than asking a general model to emit an entire MIDI:
the DSP tracker proposes p-1/p/p+1, and the model only selects the candidate
whose pitch matches the source clip. Candidate order is randomized and each
note is repeated so position bias can be measured instead of mistaken for
pitch understanding.
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


def wav_bytes(samples: np.ndarray, rate: int = 16000) -> bytes:
    samples = np.clip(samples, -1, 1)
    stream = io.BytesIO()
    with wave.open(stream, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes((samples * 32767).astype("<i2").tobytes())
    return stream.getvalue()


def build_probe(samples: np.ndarray, note: dict[str, object], pitches: list[int], source_pitch: int) -> bytes:
    rate = 16000
    start = max(0, int(float(note["start"]) * rate))
    end = min(len(samples), int((float(note["start"]) + float(note["duration"])) * rate))
    source = samples[start:end]
    if len(source) < int(0.2 * rate):
        raise ValueError("note clip too short")
    source = source[: int(0.55 * rate)]
    t = np.arange(len(source)) / rate
    chunks = [source, np.zeros(int(0.18 * rate))]
    for pitch in pitches:
        # Pitch-shift the original clip by resampling, then fit it back to the
        # same duration. Keeping the singer's timbre makes the comparison a
        # fairer multimodal A/B than comparing voice against a sine wave.
        ratio = 2 ** ((pitch - source_pitch) / 12)
        resampled_length = max(2, int(round(len(source) / ratio)))
        resampled = np.interp(
            np.linspace(0, len(source) - 1, resampled_length),
            np.arange(len(source)), source,
        )
        shifted = np.interp(
            np.linspace(0, resampled_length - 1, len(source)),
            np.arange(resampled_length), resampled,
        )
        chunks.extend([shifted, np.zeros(int(0.18 * rate))])
    return wav_bytes(np.concatenate(chunks))


async def ask(client: httpx.AsyncClient, endpoint: str, key: str, model: str, audio: bytes) -> str:
    prompt = (
        "音频先播放一小段人声哼唱，随后依次播放候选 A、B、C 三个纯音。"
        "只比较音高，不比较音色；选择与开头哼唱音高最接近的候选。"
        "只回答 A、B 或 C，不要解释，不要猜歌名。"
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


async def main(args: argparse.Namespace) -> None:
    load_dotenv(args.env)
    key = os.getenv("DASHSCOPE_API_KEY", "").strip()
    workspace = os.getenv("DASHSCOPE_WORKSPACE_ID", "").strip()
    endpoint = f"https://{workspace}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/chat/completions"
    model = os.getenv("QWEN_OMNI_MODEL", "qwen3.5-omni-flash")
    with wave.open(str(args.audio), "rb") as source:
        samples = np.frombuffer(source.readframes(source.getnframes()), dtype="<i2").astype(np.float64) / 32768
    notes = extract_hummed_notes(args.audio)["notes"]
    indices = [int(item) for item in args.notes.split(",") if item.strip()]
    rng = random.Random(args.seed)
    rows = []
    async with httpx.AsyncClient(timeout=120) as client:
        for index in indices:
            note = notes[index]
            base = int(note["pitch"])
            canonical = [base - 1, base, base + 1]
            trials = []
            for repeat in range(max(1, args.repeats)):
                candidates = canonical[:]
                rng.shuffle(candidates)
                answer = await ask(client, endpoint, key, model, build_probe(samples, note, candidates, base))
                labels = {"A": 0, "B": 1, "C": 2}
                selected = None
                label = answer[:1].upper()
                if label in labels and labels[label] < len(candidates):
                    selected = candidates[labels[label]]
                trials.append({
                    "repeat": repeat + 1,
                    "candidates": candidates,
                    "answer": answer,
                    "selected_pitch": selected,
                    "correct": selected == base,
                })
            votes = [trial["selected_pitch"] for trial in trials if trial["selected_pitch"] is not None]
            consensus = max(set(votes), key=votes.count) if votes else None
            rows.append({
                "index": index,
                "source_pitch": base,
                "trials": trials,
                "consensus_pitch": consensus,
                "consensus_correct": consensus == base,
            })
    print(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("audio", type=Path)
    parser.add_argument("--notes", default="2,4,8,10")
    parser.add_argument("--repeats", type=int, default=3, help="每个音符随机候选顺序的重复次数")
    parser.add_argument("--seed", type=int, default=7, help="候选顺序随机种子")
    parser.add_argument("--env", type=Path, default=Path(".env"))
    asyncio.run(main(parser.parse_args()))
