"""Optional cloud-memory check for correcting clearly misheard melody pitches.

The signal tracker supplies timing and measured pitches.  A text model is used
only as a semantic recognizer: it may return a replacement pitch sequence when
it recognizes a very similar familiar melody from the sequence itself.  It
never receives a title, a reference score, web search results, or permission
to change starts/durations.  An uncertain or failed call returns the measured
sequence unchanged.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

import httpx


class SemanticPitchError(RuntimeError):
    """The optional semantic correction call could not be completed."""


def _endpoint() -> str:
    configured = os.getenv("DASHSCOPE_BASE_URL", "").strip().rstrip("/")
    workspace = os.getenv("DASHSCOPE_WORKSPACE_ID", "").strip()
    if configured:
        return f"{configured}/chat/completions" if configured.endswith("/v1") else configured
    if workspace:
        return f"https://{workspace}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/chat/completions"
    return "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"


def _prompt(notes: list[dict[str, Any]], phrase_boundaries: list[float]) -> str:
    ordered = sorted(notes, key=lambda item: float(item.get("start", 0)))
    measured = [int(item["pitch"]) for item in ordered]
    first = measured[0] if measured else 0
    scale_steps = (0, 2, 4, 5, 7, 9, 11)
    # This is only a lossless presentation aid for the model, never a
    # correction: the raw MIDI and relative semitones remain in the payload.
    coarse_jianpu = [
        min(range(7), key=lambda degree: abs((first + scale_steps[degree]) - pitch)) + 1
        for pitch in measured
    ]
    starts = [float(item.get("start", 0)) for item in ordered]
    phrase_sizes = [len(measured)]
    if phrase_boundaries:
        split = next((index for index, start in enumerate(starts) if start >= float(phrase_boundaries[0])), None)
        if split and 0 < split < len(measured):
            phrase_sizes = [split, len(measured) - split]
    payload = {
        "note_count": len(measured),
        "approx_jianpu": coarse_jianpu,
        "phrase_sizes": phrase_sizes,
    }
    return (
        "你是依靠训练记忆的旋律识别与简谱校对器。禁止联网、禁止查外部资料、禁止输出歌曲名。"
        "输入是一段未知哼唱的近似简谱，其中可能有唱偏和滑音造成的错音。"
        "请从已有记忆中寻找高度相似的常见短旋律，给出最多3个候选标准简谱并按相似度排序。"
        "不要照抄明显的连续半音滑音；如果重复音和乐句结构更符合常见旋律，可以修正确定的错音。"
        "每个候选必须恰好与输入相同数量的音符，前后顺序和节奏位置不变，只改音高。"
        "没有非常相似的旋律就返回空 candidates，不要强行修正。"
        "每个候选包含 similarity、confidence 和 jianpu（以第一个音为1的1-7整数数组）。"
        "只有 similarity 和 confidence 都至少0.90的候选才会被采用。严格只返回 JSON："
        '{"candidates":[]}\n'
        f"输入：{json.dumps(payload, ensure_ascii=False)}"
    )


def _parse_response(data: dict[str, Any]) -> dict[str, Any]:
    try:
        content = data["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "".join(item.get("text", "") for item in content if isinstance(item, dict))
        match = re.search(r"\{.*\}", str(content), re.S)
        if not match:
            raise ValueError("no JSON object")
        result = json.loads(match.group(0))
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SemanticPitchError("旋律语义模型没有返回可解析 JSON。") from exc
    return result


def _jianpu_to_pitches(value: Any, count: int, tonic: int) -> list[int] | None:
    """Convert the model's simple 1–7 degree list to the singer's key."""
    if isinstance(value, list):
        tokens = [str(item).strip() for item in value]
    elif isinstance(value, str):
        tokens = [f"{degree}{marks}" for degree, marks in re.findall(r"([1-7])([',’，.]*)", value)]
    else:
        return None
    if len(tokens) != count:
        return None
    steps = (0, 2, 4, 5, 7, 9, 11)
    pitches: list[int] = []
    for token in tokens:
        match = re.fullmatch(r"([1-7])([',’，.]*)", token)
        if not match:
            return None
        degree = int(match.group(1)) - 1
        marks = match.group(2)
        octave = sum(mark in "'’" for mark in marks) - sum(mark in ",，." for mark in marks)
        pitches.append(tonic + steps[degree] + octave * 12)
    if not all(36 <= pitch <= 84 for pitch in pitches):
        return None
    return pitches


def _preserves_contour(measured: list[int], candidate: list[int]) -> bool:
    """Reject a semantic guess that reverses a clear measured direction."""
    if len(measured) != len(candidate) or len(measured) < 3:
        return False
    measured_steps = [b - a for a, b in zip(measured, measured[1:])]
    candidate_steps = [b - a for a, b in zip(candidate, candidate[1:])]
    comparable = [
        (old, new) for old, new in zip(measured_steps, candidate_steps)
        if old and new
    ]
    if comparable:
        agreement = sum((old > 0) == (new > 0) for old, new in comparable) / len(comparable)
        if agreement < 0.80:
            return False
    # A run of at least three measured descending steps is a useful guard
    # against a remembered jianpu sequence jumping up in the middle of a
    # falling phrase.  Flat corrected pairs are allowed.
    for index in range(len(measured_steps) - 2):
        run = measured_steps[index:index + 3]
        if all(step < 0 for step in run) and any(step > 0 for step in candidate_steps[index:index + 3]):
            return False
        if all(step > 0 for step in run) and any(step < 0 for step in candidate_steps[index:index + 3]):
            return False
    return True


def _candidate(result: dict[str, Any], notes: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    ordered = sorted((dict(note) for note in notes), key=lambda item: float(item.get("start", 0)))
    measured = [int(note["pitch"]) for note in ordered]
    # The model returns ranked candidates; only the top candidate can pass the
    # high-similarity gate. Accept the old flat shape in tests/older logs too.
    ranked = result.get("candidates") if isinstance(result, dict) else None
    selected_result = ranked[0] if isinstance(ranked, list) and ranked and isinstance(ranked[0], dict) else result
    if not isinstance(selected_result, dict):
        selected_result = {}
    try:
        similar = bool(selected_result.get("similar", bool(ranked)))
        similarity = float(selected_result.get("similarity", 0))
        confidence = float(selected_result.get("confidence", 0))
    except (TypeError, ValueError):
        similar, similarity, confidence = False, 0.0, 0.0
    raw_pitches = selected_result.get("pitches")
    valid = isinstance(raw_pitches, list) and len(raw_pitches) == len(ordered)
    if valid:
        try:
            model_pitches = [int(value) for value in raw_pitches]
            pitches = model_pitches[:]
            valid = all(36 <= pitch <= 84 for pitch in pitches)
        except (TypeError, ValueError):
            valid = False
    else:
        pitches = []
    # Models sometimes express a remembered melody around C4 even though the
    # singer used another key. Preserve the measured first note as anchor.
    model_anchored: list[int] | None = None
    jianpu_pitches = _jianpu_to_pitches(selected_result.get("jianpu"), len(measured), measured[0])
    if valid and pitches:
        shift = measured[0] - pitches[0]
        if abs(shift) <= 24:
            model_anchored = [pitch + shift for pitch in pitches]
            valid = all(36 <= pitch <= 84 for pitch in model_anchored)
    # Prefer numbered notation only when it agrees with the model's absolute
    # sequence. This catches malformed responses where the explanation and
    # pitch array describe different melodies.
    if jianpu_pitches is not None and (model_anchored is None or all(
        abs(a - b) <= 1 for a, b in zip(jianpu_pitches, model_anchored)
    )):
        pitches = jianpu_pitches
        valid = True
    elif model_anchored is not None:
        pitches = model_anchored
    diagnostic = {
        "mode": "measured",
        "model": os.getenv("QWEN_MELODY_MODEL", "qwen3.5-plus"),
        "similar": similar,
        "similarity": round(max(0.0, min(1.0, similarity)), 3),
        "confidence": round(max(0.0, min(1.0, confidence)), 3),
        "changed_notes": 0,
        "measured_pitches": measured,
        "candidate_pitches": pitches,
        "jianpu": selected_result.get("jianpu", []),
    }
    threshold = float(os.getenv("QWEN_MELODY_MIN_SIMILARITY", "0.90"))
    if not (similar and valid and similarity >= threshold and confidence >= threshold):
        diagnostic["reason"] = "no-high-confidence-match"
        return ordered, diagnostic
    if not _preserves_contour(measured, pitches):
        diagnostic["reason"] = "contour-conflict"
        return ordered, diagnostic
    changed = sum(a != b for a, b in zip(measured, pitches))
    if not changed:
        diagnostic["reason"] = "match-without-pitch-change"
        return ordered, diagnostic
    corrected = []
    for note, pitch in zip(ordered, pitches):
        item = dict(note)
        item["pitch"] = pitch
        item["pitch_cents"] = 0.0
        item["quantization_margin_cents"] = 50.0
        item["semantic_source_pitch"] = int(note["pitch"])
        corrected.append(item)
    diagnostic["mode"] = "semantic-memory"
    diagnostic["changed_notes"] = changed
    return corrected, diagnostic


async def apply_semantic_pitch_correction(
    notes: list[dict[str, Any]], phrase_boundaries: list[float] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Ask the cloud text model for a high-confidence memorized melody match."""
    if len(notes) < 2:
        return notes, {"mode": "measured", "status": "insufficient-notes", "changed_notes": 0}
    key = os.getenv("DASHSCOPE_API_KEY", "").strip()
    if not key:
        raise SemanticPitchError("未配置 DASHSCOPE_API_KEY。")
    model = os.getenv("QWEN_MELODY_MODEL", "qwen3.5-plus").strip() or "qwen3.5-plus"
    try:
        timeout = max(10.0, float(os.getenv("QWEN_MELODY_TIMEOUT_SECONDS", "45")))
    except ValueError:
        timeout = 45.0
    payload = {
        "model": model,
        "stream": False,
        "temperature": 0.0,
        "max_tokens": 300,
        "enable_thinking": False,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": "只输出一行合法 JSON，不输出歌曲名。"},
            {"role": "user", "content": _prompt(notes, phrase_boundaries or [])},
        ],
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=15)) as client:
            response = await client.post(_endpoint(), headers={"Authorization": f"Bearer {key}"}, json=payload)
            if response.is_error:
                raise SemanticPitchError(f"旋律语义模型 API {response.status_code}: {response.text[:500]}")
            data = response.json()
    except SemanticPitchError:
        raise
    except httpx.HTTPError as exc:
        raise SemanticPitchError(f"连接旋律语义模型失败：{exc}") from exc
    return _candidate(_parse_response(data), notes)
