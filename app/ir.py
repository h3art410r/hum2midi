"""Canonical, versioned MIDI-backed melody IR helpers."""

from __future__ import annotations

import io
from typing import Any

import mido


def melody_to_midi(analysis: dict[str, Any]) -> tuple[bytes, dict[str, Any]]:
    """Normalize measured note events and cloud validation into MIDI-backed IR."""
    bpm = float(analysis["tempo_bpm"])
    midi = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    midi.tracks.append(track)
    track.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(bpm), time=0))
    if analysis.get("midi_key_signature"):
        track.append(mido.MetaMessage("key_signature", key=analysis["midi_key_signature"], time=0))
    events = []
    for note in analysis["notes"]:
        start = max(0.0, float(note["start"]))
        end = start + max(0.04, float(note["duration"]))
        velocity = int(note.get("velocity", 88))
        pitch = int(note["pitch"])
        events.append((round(start * midi.ticks_per_beat * bpm / 60), 1, pitch, velocity))
        events.append((round(end * midi.ticks_per_beat * bpm / 60), 0, pitch, 0))
    events.sort(key=lambda event: (event[0], event[1]))
    previous_tick = 0
    for tick, is_on, pitch, velocity in events:
        track.append(mido.Message(
            "note_on" if is_on else "note_off",
            note=pitch,
            velocity=velocity,
            time=max(0, tick - previous_tick),
        ))
        previous_tick = tick
    track.append(mido.MetaMessage("end_of_track", time=0))
    stream = io.BytesIO()
    midi.save(file=stream)
    midi_bytes = stream.getvalue()
    ir = midi_summary(midi_bytes)
    ir["key"] = analysis.get("key_name")
    ir["phrase_boundaries"] = analysis.get("phrase_boundaries", [])
    if analysis.get("model_contour"):
        ir["cloud_pitch_contour"] = analysis["model_contour"]
    ir["confidence"] = round(sum(n["confidence"] for n in analysis["notes"]) / len(analysis["notes"]), 3)
    ir["note_events"] = [
        {key: note[key] for key in (
            "pitch", "start", "duration", "velocity", "confidence",
            "quantization_margin_cents",
        ) if key in note}
        for note in sorted(analysis["notes"], key=lambda item: item["start"])
    ]
    ir["source"] = analysis.get("source", "qwen-omni-audio-understanding")
    return midi_bytes, ir


def midi_summary(data: bytes) -> dict[str, Any]:
    """Validate a Standard MIDI File and extract a compact note-event view."""
    try:
        midi = mido.MidiFile(file=io.BytesIO(data))
    except Exception as exc:
        raise ValueError("Cloud transcription did not return a valid MIDI file") from exc

    tempo = 500_000
    ticks = 0
    active: dict[tuple[int, int], list[tuple[int, int]]] = {}
    notes: list[dict[str, Any]] = []
    for message in midi.merged_track:
        ticks += message.time
        if message.type == "set_tempo":
            tempo = message.tempo
        elif message.type == "note_on" and message.velocity > 0:
            active.setdefault((message.channel, message.note), []).append((ticks, message.velocity))
        elif message.type == "note_off" or (message.type == "note_on" and message.velocity == 0):
            key = (message.channel, message.note)
            starts = active.get(key)
            if starts:
                start_tick, velocity = starts.pop(0)
                start = mido.tick2second(start_tick, midi.ticks_per_beat, tempo)
                end = mido.tick2second(ticks, midi.ticks_per_beat, tempo)
                notes.append({"pitch": key[1], "start": round(start, 4), "duration": round(max(0, end - start), 4), "velocity": velocity})

    if not notes:
        raise ValueError("The cloud MIDI transcription contains no melody notes")
    notes.sort(key=lambda note: note["start"])
    duration = max(note["start"] + note["duration"] for note in notes)
    bpm = round(60_000_000 / tempo, 2)
    return {
        "ir_version": "1.0",
        "kind": "MelodyIR",
        "midi_format": "SMF",
        "midi_version": 1,
        "ticks_per_beat": midi.ticks_per_beat,
        "tempo_bpm": bpm,
        "time_signature": None,
        "phrase_boundaries": [],
        "key": None,
        "confidence": None,
        "identity_constraints": ["preserve_pitch_contour", "preserve_rhythm"],
        "duration_seconds": round(duration, 4),
        "note_events": notes,
    }
