"""Cloud MIDI-semantic style conversion and deterministic MIDI serialization."""

from __future__ import annotations

import io
import json
import logging
import os
import re
from typing import Any

import httpx
import mido

logger = logging.getLogger(__name__)

class MidiStyleError(RuntimeError):
    pass


class QwenMidiStyleClient:
    """Use a text model to convert a canonical melody into a complete style MIDI."""

    def __init__(self) -> None:
        self.api_key = os.getenv("DASHSCOPE_API_KEY", "").strip()
        self.workspace_id = os.getenv("DASHSCOPE_WORKSPACE_ID", "").strip()
        self.model = os.getenv("QWEN_TEXT_MODEL", "qwen-flash").strip()
        try:
            self.max_tokens = max(512, min(4096, int(os.getenv("QWEN_TEXT_MAX_TOKENS", "3072"))))
        except ValueError:
            self.max_tokens = 3072
        try:
            self.temperature = max(0.0, min(1.2, float(os.getenv("QWEN_TEXT_TEMPERATURE", "0.85"))))
        except ValueError:
            self.temperature = 0.85
        configured = os.getenv("DASHSCOPE_BASE_URL", "").strip().rstrip("/")
        if configured:
            self.endpoint = f"{configured}/chat/completions" if configured.endswith("/v1") else configured
        elif self.workspace_id:
            self.endpoint = f"https://{self.workspace_id}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/chat/completions"
        else:
            self.endpoint = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
        if not self.api_key:
            raise MidiStyleError("请在服务端配置 DASHSCOPE_API_KEY。")

    async def expand(self, ir: dict[str, Any], style: str, contour: str) -> dict[str, Any]:
        if style not in {"funk", "lofi"}:
            raise MidiStyleError(f"不支持的风格：{style}")
        style_instruction = {
            "funk": "重新创作成辨识度很强的 Funk：短促电吉他/Clav 切分、带幽灵音的律动低音、反拍军鼓和有变化的鼓句。",
            "lofi": "重新创作成辨识度很强的 Lofi：温暖电钢琴、爵士七/九和弦、松弛的 swing、稀疏鼓点、留白和磁带回声。",
        }[style]
        source = {
            "tempo_bpm": ir.get("tempo_bpm", 100),
            "duration_seconds": round(float(ir.get("duration_seconds", 0)), 3),
            "phrase_boundaries": ir.get("phrase_boundaries", []),
            "rhythm_template": [
                {"start": round(float(note.get("start", 0)), 3), "duration": round(float(note.get("duration", 0)), 3)}
                for note in ir.get("note_events", [])
            ],
            "lead_notes": [
                {key: note[key] for key in ("pitch", "start", "duration", "velocity") if key in note}
                for note in ir.get("note_events", [])
            ],
            "audio_contour_check": contour,
        }
        prompt = (
            "你是专业编曲人和 MIDI 作曲器。请先从输入 MIDI 中听懂一条单声部哼唱的核心动机、音程轮廓、重复音、乐句顺序和停顿位置，"
            "再把它重新编写成一段有完整起承转合的音乐。"
            f"目标风格：{style_instruction}"
            "这不是给原 MIDI 加一条伴奏，也不是做两小节循环；输出必须是对原旋律的整体风格化重编。"
            "主奏可以重新选择调式和音区，改写音高、装饰音、和声和段落密度，但必须把 rhythm_template 当作不可破坏的演唱节奏："
            "主奏的核心音符按顺序使用这些 start 和 duration，不能把开头静音或 phrase_boundaries 中的句间停顿压掉。"
            "允许在核心音符之间加入短经过音、幽灵音或呼应，但不要移动核心起音；主奏要保留原动机的可辨认轮廓和乐句先后。"
            "不能逐音复制输入 lead_notes，也不能只改 program；要通过新音高组织、和声、律动和音色完成真正重编。"
            "请写出覆盖输入旋律时长的完整段落，允许短前奏、间奏和尾奏，避免每拍或每小节机械重复。"
            "把 phrase_boundaries 当作真实的段落边界：边界前是 A 段，边界后是 B 段；B 段必须至少改变一次和声、主奏音区或节奏密度，最后 0.5–1 秒做自然收束。"
            "同一条声部的连续四个音符模式最多重复一次，不要把同一组 bass 或鼓点从头复制到尾。"
            "至少输出三条有明确分工且彼此配合的声部：一个重新编写的 lead、一个和声/键盘或吉他、一个 bass；"
            "如果目标风格需要，再加入简洁但有变化的鼓组。每条声部 8 到 32 个音符，音符数量少时优先保证旋律和段落完整。"
            "和声至少发生两次和弦色彩变化，bass 至少使用三个不同音高并加入经过音；鼓组至少同时包含底鼓与军鼓或镲片。"
            "不要让整段 bass 只有一个音，也不要让所有声部落在完全相同的等间隔网格上。"
            "质量底线：若输入有 8 个以上音符，lead_reimagined 至少写 12 个音符并使用至少 6 个不同音高；"
            "主奏、和声和 bass 都要延伸到输入时长的末段，不能只写开头几秒。"
            "主奏的音高变化要有音乐逻辑：以级进、和弦内音和原动机的轮廓为主，避免无关的随机大跳；把惊喜放在切分、音色、和声色彩和段落动态上。"
            "Funk 要有切分、反拍、律动低音和鼓的层次，至少一半的伴奏起音落在非强拍或十六分位置，并安排一小段 fill；"
            "Lofi 要有明显但不过度的 swing（不要所有音符等间隔）、爵士七和弦/九和弦色彩、低密度鼓点和有呼吸感的留白。"
            "所有 start/duration 使用秒，范围应覆盖输入的 duration_seconds，program 使用 General MIDI 0 到 127；tempo_bpm 必须沿用输入值，风格律动用切分和 swing 表现。"
            "只返回严格 JSON，不要 Markdown 或解释。为节省输出，notes 必须使用 [pitch,start,duration,velocity] 数组。JSON 格式必须是："
            '{"tempo_bpm":100,"tracks":[{"name":"lead_reimagined","program":80,"notes":[[60,0,0.5,90]]},{"name":"harmony","program":4,"notes":[[48,0,1.0,65]]},{"name":"bass","program":34,"notes":[[36,0,0.5,90]]}]}'
            f"\n输入 JSON：{json.dumps(source, ensure_ascii=False)}"
        )
        payload = {
            "model": self.model,
            "stream": False,
            # Funk benefits from a slightly steadier melodic contour; keep
            # Lofi at the configured creative temperature.
            "temperature": min(self.temperature, 0.75) if style == "funk" else self.temperature,
            "max_tokens": self.max_tokens,
            # This is a raw HTTP request, so Model Studio's extension field
            # must be top-level (extra_body is only for SDK calls).
            "enable_thinking": False,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": "你只输出严格合法的 JSON MIDI 风格转换方案。"},
                {"role": "user", "content": prompt},
            ],
        }
        async def request_model(request_prompt: str) -> dict[str, Any]:
            payload["messages"][1]["content"] = request_prompt
            try:
                logger.info("midi_style_request start model=%s style=%s", self.model, style)
                async with httpx.AsyncClient(timeout=httpx.Timeout(60, connect=15)) as client:
                    response = await client.post(
                        self.endpoint,
                        headers={"Authorization": f"Bearer {self.api_key}"},
                        json=payload,
                    )
                    if response.is_error:
                        body = response.text[:800]
                        raise MidiStyleError(f"MIDI 风格模型 API {response.status_code}: {body}")
                    data = response.json()
                logger.info("midi_style_request complete model=%s style=%s", self.model, style)
            except httpx.TimeoutException as exc:
                logger.warning("midi_style_request timeout model=%s style=%s error=%s", self.model, style, exc)
                raise MidiStyleError("MIDI 风格模型请求超时（等待超过 60 秒）。") from exc
            except httpx.HTTPError as exc:
                logger.warning("midi_style_request http_error model=%s style=%s error=%s", self.model, style, exc)
                raise MidiStyleError(f"连接 MIDI 风格模型失败：{exc}") from exc
            try:
                content = data["choices"][0]["message"]["content"]
                if isinstance(content, list):
                    content = "".join(item.get("text", "") for item in content if isinstance(item, dict))
                match = re.search(r"\{.*\}", str(content), re.S)
                if not match:
                    raise ValueError("no JSON object")
                plan = json.loads(match.group(0))
            except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise MidiStyleError("MIDI 风格模型没有返回可解析的 JSON。") from exc
            return validate_arrangement(plan, ir)

        try:
            plan = await request_model(prompt)
        except MidiStyleError as exc:
            # Flash occasionally truncates a long JSON response. One compact
            # retry is cheaper and safer than failing an otherwise valid job;
            # transport/API errors still propagate immediately.
            if "没有返回可解析的 JSON" not in str(exc):
                raise
            logger.info("midi_style_parse_retry style=%s", style)
            plan = await request_model(
                prompt
                + "\n上一版 JSON 被截断。请显著压缩输出：每条声部最多 20 个音符，"
                "只使用数组格式，不要任何解释文字。"
            )
        issues = style_quality_issues(plan, ir, style)
        if issues:
            logger.info("midi_style_quality_retry style=%s issues=%s", style, ",".join(issues))
            retry_prompt = (
                prompt
                + "\n上一版没有通过成片质量检查（"
                + "、".join(issues)
                + "）。请重新完整编曲，修正这些问题后只返回 JSON。"
            )
            plan = await request_model(retry_prompt)
        # The model is free to redesign harmony, pitch and texture, but its
        # lead timing must still feel like the user's performance.  Models
        # occasionally start at t=0 or flatten the inter-phrase pause; anchor
        # the returned arrangement back to the measured source timeline.
        return anchor_style_plan(plan, ir, style)


def validate_arrangement(plan: Any, ir: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(plan, dict) or not isinstance(plan.get("tracks"), list):
        raise MidiStyleError("风格转换结果缺少 tracks 数组。")
    source_notes = ir.get("note_events", [])
    source_duration = max((float(n.get("start", 0)) + float(n.get("duration", 0)) for n in source_notes), default=1.0)
    duration_limit = max(15.0, source_duration + 4.0)
    tracks: list[dict[str, Any]] = []
    for raw_track in plan["tracks"]:
        if not isinstance(raw_track, dict) or not isinstance(raw_track.get("name"), str):
            continue
        name = raw_track["name"]
        notes: list[dict[str, Any]] = []
        for raw_note in raw_track.get("notes", [])[:96]:
            try:
                if isinstance(raw_note, (list, tuple)):
                    pitch = int(raw_note[0])
                    start = max(0.0, float(raw_note[1]))
                    length = float(raw_note[2])
                    velocity = int(raw_note[3]) if len(raw_note) > 3 else 72
                elif isinstance(raw_note, dict):
                    pitch = int(raw_note["pitch"])
                    start = max(0.0, float(raw_note["start"]))
                    length = float(raw_note["duration"])
                    velocity = int(raw_note.get("velocity", 72))
                else:
                    continue
            except (KeyError, TypeError, ValueError):
                continue
            if 0 <= pitch <= 127 and 0 <= start < duration_limit and 0.04 <= length <= 4:
                notes.append({"pitch": pitch, "start": round(start, 4), "duration": round(min(length, duration_limit - start), 4), "velocity": max(1, min(127, velocity))})
        try:
            program = int(raw_track.get("program", 0))
        except (TypeError, ValueError):
            program = 0
        tracks.append({"name": name[:64], "program": max(0, min(127, program)), "notes": sorted(notes, key=lambda note: (note["start"], note["pitch"]))})
    tracks = tracks[:16]
    if not tracks or not any(track["notes"] for track in tracks):
        raise MidiStyleError("风格转换结果没有可用音符。")
    try:
        tempo = float(plan.get("tempo_bpm", ir.get("tempo_bpm", 100)))
    except (TypeError, ValueError):
        tempo = float(ir.get("tempo_bpm", 100))
    return {"tempo_bpm": max(50.0, min(200.0, tempo)), "tracks": tracks}


def anchor_style_plan(plan: dict[str, Any], ir: dict[str, Any], style: str) -> dict[str, Any]:
    """Keep source phrase timing audible while retaining model-created notes.

    The text model should design the arrangement, but a free-form response can
    silently move the first note to zero, stretch the phrase, or fill a pause.
    We therefore use the model's lead *pitches* on the source note grid and
    time-warp the accompaniment onto the same start/end window.  This is a
    timing correction only; it does not copy the source pitches into the
    arrangement or synthesize a missing style.
    """
    source_notes = sorted(
        (dict(note) for note in ir.get("note_events", []) if float(note.get("duration", 0)) >= 0.04),
        key=lambda note: float(note.get("start", 0)),
    )
    if not source_notes:
        return plan
    source_start = min(float(note.get("start", 0)) for note in source_notes)
    source_end = max(float(note.get("start", 0)) + float(note.get("duration", 0)) for note in source_notes)
    source_duration = max(0.1, source_end - source_start)

    tracks = [dict(track) for track in plan.get("tracks", [])]
    lead_index = next((i for i, track in enumerate(tracks) if "lead" in str(track.get("name", "")).lower()), 0)
    if tracks:
        raw_lead = list(tracks[lead_index].get("notes", []))
        # Select model pitches by musical progress, then put those pitches on
        # the exact source starts/durations.  This keeps a changed melody
        # recognizable as the same hummed phrase, including its pause.
        model_lead = sorted(raw_lead, key=lambda note: float(note.get("start", 0)))
        if model_lead:
            core: list[dict[str, Any]] = []
            for index, source in enumerate(source_notes):
                pick = round(index * (len(model_lead) - 1) / max(1, len(source_notes) - 1))
                chosen = model_lead[pick]
                try:
                    pitch = max(0, min(127, int(chosen.get("pitch", source.get("pitch", 60)))))
                except (TypeError, ValueError):
                    pitch = int(source.get("pitch", 60))
                try:
                    velocity = max(1, min(127, int(chosen.get("velocity", 88))))
                except (TypeError, ValueError):
                    velocity = 88
                core.append({
                    "pitch": pitch,
                    "start": round(float(source.get("start", 0)), 4),
                    "duration": round(float(source.get("duration", 0)), 4),
                    "velocity": velocity,
                })
            tracks[lead_index]["notes"] = core

    # Find measurable silences in the source.  Accompaniment notes are clipped
    # at these windows so a dense generated backing cannot mask the hummed
    # phrase boundary.
    pauses: list[tuple[float, float]] = []
    for previous, current in zip(source_notes, source_notes[1:]):
        gap_start = float(previous.get("start", 0)) + float(previous.get("duration", 0))
        gap_end = float(current.get("start", 0))
        if gap_end - gap_start >= 0.22:
            pauses.append((gap_start, gap_end))

    # Warp non-lead notes from the model's time span to the source span.  Keep
    # the model's relative rhythm and variation, only restoring the user's
    # leading silence, ending and any explicit inter-phrase silence.
    all_model_notes = [note for track in tracks for note in track.get("notes", []) if track is not tracks[lead_index]] if tracks else []
    model_start = min((float(note.get("start", 0)) for note in all_model_notes), default=0.0)
    model_end = max((float(note.get("start", 0)) + float(note.get("duration", 0)) for note in all_model_notes), default=source_duration)
    model_span = max(0.1, model_end - model_start)
    scale = source_duration / model_span
    for index, track in enumerate(tracks):
        if index == lead_index:
            continue
        warped: list[dict[str, Any]] = []
        for note in track.get("notes", []):
            try:
                start = source_start + (float(note.get("start", 0)) - model_start) * scale
                duration = max(0.04, float(note.get("duration", 0)) * scale)
            except (TypeError, ValueError):
                continue
            start = max(source_start, min(source_end - 0.04, start))
            end = min(source_end, start + duration)
            # Clip a backing note that crosses a source pause.  A second half
            # is intentionally omitted: the next generated event supplies the
            # re-entry and the pause remains audible on a phone speaker.
            for gap_start, gap_end in pauses:
                if start < gap_start < end:
                    end = gap_start
                    break
                if gap_start <= start < gap_end:
                    start = gap_end
                    end = min(source_end, start + duration)
            if end - start < 0.04:
                continue
            item = dict(note)
            item["start"] = round(start, 4)
            item["duration"] = round(end - start, 4)
            warped.append(item)
        track["notes"] = warped
    # If a conservative model still copied every source pitch, apply a small
    # style-specific transposition while keeping the measured contour and
    # rhythm. This prevents a byte-for-byte "style" MIDI on an otherwise
    # valid response; the normal path remains model-generated pitches.
    if tracks and source_notes:
        lead_notes = tracks[lead_index].get("notes", [])
        source_pitches = [int(note.get("pitch", 60)) for note in source_notes]
        lead_pitches = [int(note.get("pitch", 60)) for note in lead_notes]
        if len(lead_pitches) == len(source_pitches) and lead_pitches == source_pitches:
            offset = 7 if style == "funk" else 12 if style == "lofi" else 0
            for note in lead_notes:
                note["pitch"] = max(0, min(127, int(note["pitch"]) + offset))

    if style == "funk":
        _ensure_funk_backbeat(tracks, source_notes, float(ir.get("tempo_bpm", 100)))
        _ensure_funk_groove(tracks, source_notes, float(ir.get("tempo_bpm", 100)))
    elif style == "lofi":
        _shape_lofi_texture(tracks, source_notes, float(ir.get("tempo_bpm", 100)))

    plan = dict(plan)
    plan["tracks"] = tracks
    # A tempo change makes a familiar hummed phrase feel like a different
    # song even when note starts are anchored. Keep the source BPM; style
    # identity comes from pitch voicing, syncopation and timbre.
    try:
        plan["tempo_bpm"] = float(ir.get("tempo_bpm", plan.get("tempo_bpm", 100)))
    except (TypeError, ValueError):
        pass
    return plan


def _ensure_funk_backbeat(tracks: list[dict[str, Any]], source_notes: list[dict[str, Any]], tempo_bpm: float) -> None:
    """Fill only missing Funk backbeat voices, leaving model drum ideas intact."""
    drum = next((track for track in tracks if "drum" in str(track.get("name", "")).lower() or "perc" in str(track.get("name", "")).lower()), None)
    if drum is None:
        drum = {"name": "funk_drums", "program": 0, "notes": []}
        tracks.append(drum)
    notes = list(drum.get("notes", []))
    existing = {(int(note.get("pitch", 0)), round(float(note.get("start", 0)), 3)) for note in notes}
    beat = 60.0 / max(50.0, min(200.0, tempo_bpm))
    source_end = max((float(note.get("start", 0)) + float(note.get("duration", 0)) for note in source_notes), default=0.0)
    for index, source in enumerate(source_notes):
        base = float(source.get("start", 0))
        # Snare on the backbeat and a lighter hat on the late eighth.  These
        # subdivisions fit inside each measured hummed event and are skipped
        # automatically if the source has a phrase pause there.
        for pitch, offset, velocity, length in (
            (38, beat * 0.5, 96, 0.07),
            (42, beat * 0.75, 58, 0.05),
        ):
            start = base + offset
            if start >= source_end - 0.03:
                continue
            if any(float(previous.get("start", 0)) + float(previous.get("duration", 0)) <= start <= float(current.get("start", 0)) for previous, current in zip(source_notes, source_notes[1:])):
                continue
            key = (pitch, round(start, 3))
            if key not in existing:
                notes.append({"pitch": pitch, "start": round(start, 4), "duration": length, "velocity": velocity})
                existing.add(key)
        if index % 2 == 1:
            start = base + beat * 0.25
            if start < source_end - 0.03:
                key = (40, round(start, 3))
                if key not in existing:
                    notes.append({"pitch": 40, "start": round(start, 4), "duration": 0.05, "velocity": 46})
                    existing.add(key)
    drum["notes"] = sorted(notes, key=lambda note: (float(note.get("start", 0)), int(note.get("pitch", 0))))


def _ensure_funk_groove(tracks: list[dict[str, Any]], source_notes: list[dict[str, Any]], tempo_bpm: float) -> None:
    """Add syncopated ghost notes inside the model's existing Funk parts."""
    beat = 60.0 / max(50.0, min(200.0, tempo_bpm))
    source_end = max((float(n.get("start", 0)) + float(n.get("duration", 0)) for n in source_notes), default=0.0)
    pauses = [
        (float(a.get("start", 0)) + float(a.get("duration", 0)), float(b.get("start", 0)))
        for a, b in zip(source_notes, source_notes[1:])
        if float(b.get("start", 0)) - (float(a.get("start", 0)) + float(a.get("duration", 0))) >= 0.22
    ]

    def in_pause(at: float) -> bool:
        return any(left < at < right for left, right in pauses)

    bass = next((track for track in tracks if "bass" in str(track.get("name", "")).lower()), None)
    harmony = next((track for track in tracks if any(word in str(track.get("name", "")).lower() for word in ("harmony", "chord", "keys"))), None)
    for index, source in enumerate(source_notes):
        base = float(source.get("start", 0))
        offbeat = base + beat * 0.75
        if offbeat >= source_end - 0.04 or in_pause(offbeat):
            continue
        if bass is not None:
            bass_notes = bass.setdefault("notes", [])
            nearest = min(bass_notes, key=lambda n: abs(float(n.get("start", 0)) - base), default={})
            try:
                root = int(nearest.get("pitch", source.get("pitch", 48)))
            except (TypeError, ValueError):
                root = 48
            pitch = max(24, min(60, root + (7 if index % 2 else 5)))
            if not any(abs(float(n.get("start", 0)) - offbeat) < 0.06 for n in bass_notes):
                bass_notes.append({"pitch": pitch, "start": round(offbeat, 4), "duration": round(min(0.16, beat * 0.28), 4), "velocity": 68})
        if harmony is not None:
            harmony_notes = harmony.setdefault("notes", [])
            nearest = min(harmony_notes, key=lambda n: abs(float(n.get("start", 0)) - base), default={})
            try:
                chord_tone = int(nearest.get("pitch", source.get("pitch", 60))) + (7 if index % 2 else 4)
            except (TypeError, ValueError):
                chord_tone = 64
            if not any(abs(float(n.get("start", 0)) - offbeat) < 0.06 for n in harmony_notes):
                harmony_notes.append({"pitch": max(36, min(96, chord_tone)), "start": round(offbeat, 4), "duration": round(min(0.12, beat * 0.22), 4), "velocity": 56})
    for track in (bass, harmony):
        if track is not None:
            track["notes"] = sorted(track.get("notes", []), key=lambda n: (float(n.get("start", 0)), int(n.get("pitch", 0))))


def _shape_lofi_texture(tracks: list[dict[str, Any]], source_notes: list[dict[str, Any]], tempo_bpm: float) -> None:
    """Turn overly busy model backing into long, breathing Lofi phrases."""
    if not source_notes:
        return
    beat = 60.0 / max(50.0, min(200.0, tempo_bpm))
    source_end = max(float(n.get("start", 0)) + float(n.get("duration", 0)) for n in source_notes)
    pauses = [
        (float(a.get("start", 0)) + float(a.get("duration", 0)), float(b.get("start", 0)))
        for a, b in zip(source_notes, source_notes[1:])
        if float(b.get("start", 0)) - (float(a.get("start", 0)) + float(a.get("duration", 0))) >= 0.22
    ]
    for track in tracks:
        name = str(track.get("name", "")).lower()
        if "lead" in name or "drum" in name or "perc" in name:
            continue
        notes = sorted(track.get("notes", []), key=lambda n: float(n.get("start", 0)))
        if len(notes) <= 8:
            continue
        # Keep the model's voicing choices, but let each selected tone ring
        # across a relaxed two-beat window instead of repeating short stabs.
        selected = notes[::2]
        shaped: list[dict[str, Any]] = []
        for index, note in enumerate(selected):
            item = dict(note)
            start = max(0.0, float(item.get("start", 0)))
            next_start = float(selected[index + 1].get("start", source_end)) if index + 1 < len(selected) else source_end
            end = start + max(float(item.get("duration", 0)), min(beat * 1.6, max(0.08, next_start - start - 0.03)))
            for gap_start, gap_end in pauses:
                if start < gap_start < end:
                    end = gap_start
                    break
                if gap_start <= start < gap_end:
                    start = gap_end
                    end = min(source_end, start + max(float(item.get("duration", 0)), beat))
            item["start"] = round(start, 4)
            item["duration"] = round(min(end, source_end) - start, 4)
            item["velocity"] = max(1, min(127, int(item.get("velocity", 64)) - 8))
            if item["duration"] >= 0.04:
                shaped.append(item)
        track["notes"] = shaped


def style_quality_issues(plan: dict[str, Any], ir: dict[str, Any], style: str) -> list[str]:
    """Return cheap musical guardrails that justify one corrective retry.

    These checks do not prescribe the notes or force source-note copying. They
    only catch the two regressions seen in sampling: a short/generic output and
    a Funk result with no offbeat or drum contrast.
    """
    tracks = plan.get("tracks", [])
    source_notes = ir.get("note_events", [])
    source_duration = max(
        (float(n.get("start", 0)) + float(n.get("duration", 0)) for n in source_notes),
        default=1.0,
    )
    issues: list[str] = []
    if len(tracks) < 3:
        issues.append("至少需要主奏、和声、低音三条声部")
    lead = next((track for track in tracks if "lead" in track.get("name", "").lower()), tracks[0] if tracks else {})
    lead_notes = lead.get("notes", [])
    if len(source_notes) >= 8 and len(lead_notes) < 12:
        issues.append("主奏音符过少")
    if len(source_notes) >= 8 and len({int(note.get("pitch", 0)) for note in lead_notes}) < 6:
        issues.append("主奏音高变化过少")
    if len(source_notes) >= 8 and len(lead_notes) == len(source_notes):
        source_pitches = [int(note.get("pitch", 0)) for note in source_notes]
        lead_pitches = [int(note.get("pitch", 0)) for note in sorted(lead_notes, key=lambda note: float(note.get("start", 0)))]
        if lead_pitches == source_pitches:
            issues.append("主奏仍逐音复制原旋律")
    if lead_notes and max(float(note.get("start", 0)) + float(note.get("duration", 0)) for note in lead_notes) < source_duration * 0.85:
        issues.append("主奏没有覆盖完整时长")
    if style == "funk" and lead_notes:
        tempo = max(50.0, float(plan.get("tempo_bpm", 100)))
        beat = 60.0 / tempo
        offbeat = sum(1 for note in lead_notes if abs(((float(note.get("start", 0)) / beat) * 2) % 1) > 0.08)
        if offbeat / len(lead_notes) < 0.2:
            issues.append("Funk 切分不足")
        drum = next((track for track in tracks if "drum" in track.get("name", "").lower() or "perc" in track.get("name", "").lower()), None)
        if drum and len({int(note.get("pitch", 0)) for note in drum.get("notes", [])}) < 2:
            issues.append("鼓组缺少音色变化")
    return issues


def arrangement_to_midi(plan: dict[str, Any], style: str | None = None) -> bytes:
    midi = mido.MidiFile(ticks_per_beat=480)
    tempo = mido.bpm2tempo(float(plan.get("tempo_bpm", 100)))
    for index, track_data in enumerate(plan.get("tracks", [])):
        track = mido.MidiTrack()
        midi.tracks.append(track)
        if index == 0:
            track.append(mido.MetaMessage("set_tempo", tempo=tempo, time=0))
        channel = 9 if track_data.get("name", "").lower() in {"drums", "drum", "percussion"} else index % 16
        if channel != 9:
            program = max(0, min(127, int(track_data.get("program", 0))))
            name = track_data.get("name", "").lower()
            # Keep the model's arrangement, but map generic lead programs to
            # recognizable target-style instruments at the MIDI boundary.
            if "lead" in name or "melody" in name:
                if style == "funk":
                    program = 27  # electric guitar / clav-like family
                elif style == "lofi":
                    program = 4   # electric piano family
            elif "bass" in name:
                # Keep the model's notes, but make the target style audible in
                # a General MIDI player even when it returned generic program 0.
                program = 38 if style == "funk" else 33 if style == "lofi" else program
            elif "harmony" in name or "chord" in name or "keys" in name or "pad" in name:
                program = 7 if style == "funk" else 89 if style == "lofi" else program
            track.append(mido.Message("program_change", channel=channel, program=program, time=0))
        events: list[tuple[float, int, int, int]] = []
        for note in track_data.get("notes", []):
            start = float(note["start"])
            end = start + float(note["duration"])
            pitch = int(note["pitch"])
            velocity = int(note.get("velocity", 72))
            events.append((start, 1, pitch, velocity))
            events.append((end, 0, pitch, 0))
        events.sort(key=lambda event: (event[0], event[1]))
        previous = 0.0
        for at, kind, pitch, velocity in events:
            tick_at = round(mido.second2tick(at, midi.ticks_per_beat, tempo))
            tick_previous = round(mido.second2tick(previous, midi.ticks_per_beat, tempo))
            delta = max(0, tick_at - tick_previous)
            if kind == 1:
                track.append(mido.Message("note_on", channel=channel, note=pitch, velocity=velocity, time=delta))
            else:
                track.append(mido.Message("note_off", channel=channel, note=pitch, velocity=0, time=delta))
            previous = at
    buffer = io.BytesIO()
    midi.save(file=buffer)
    return buffer.getvalue()

