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
    # Declare the pitch-wheel range instead of relying on a synth's default.
    # This makes the fractional cents below render consistently in FluidSynth,
    # browser players and DAWs (two semitones is the GM default range).
    for control, value in ((101, 0), (100, 0), (6, 2), (38, 0), (101, 127), (100, 127)):
        track.append(mido.Message("control_change", channel=0, control=control, value=value, time=0))
    if analysis.get("midi_key_signature"):
        track.append(mido.MetaMessage("key_signature", key=analysis["midi_key_signature"], time=0))
    events = []
    for note in analysis["notes"]:
        start = max(0.0, float(note["start"]))
        end = start + max(0.04, float(note["duration"]))
        velocity = int(note.get("velocity", 88))
        pitch = int(note["pitch"])
        start_tick = round(start * midi.ticks_per_beat * bpm / 60)
        end_tick = round(end * midi.ticks_per_beat * bpm / 60)
        # Standard GM pitch-wheel range is ±2 semitones. A measured fractional
        # pitch is emitted immediately before its note-on, preserving the
        # integer MIDI note while making playback follow the singer's actual
        # intonation. The melody is monophonic, so one channel is sufficient.
        cents = max(-99.9, min(99.9, float(note.get("pitch_cents", 0.0))))
        bend = int(round(cents / 200.0 * 8192))
        events.append((start_tick, 0, "pitchwheel", pitch, bend))
        events.append((start_tick, 1, "note_on", pitch, velocity))
        events.append((end_tick, 2, "note_off", pitch, 0))
    events.sort(key=lambda event: (event[0], event[1]))
    previous_tick = 0
    for tick, _order, message_type, pitch, value in events:
        if message_type == "pitchwheel":
            message = mido.Message("pitchwheel", pitch=value, time=max(0, tick - previous_tick))
        else:
            message = mido.Message(message_type, note=pitch, velocity=value, time=max(0, tick - previous_tick))
        track.append(message)
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
            "pitch_cents", "quantization_margin_cents",
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
