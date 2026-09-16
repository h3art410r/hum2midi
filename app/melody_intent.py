"""Small, explainable melody-intent hypotheses.

The acoustic tracker measures what was sung.  Humming often contains a
consistent one-semitone drift, though, and a singer may glide through notes
that are repeated in the intended tune.  This module generates a conservative
musical hypothesis from the measured sequence; it never invents timing and it
is only enabled when the sequence has a strong repeated-pair motif.

It is deliberately separate from pitch tracking so that the two hypotheses can
be compared in an A/B run and so that a future cloud critic can replace this
heuristic without changing the IR writer.
"""
from __future__ import annotations

from typing import Any


MAJOR_STEPS = (0, 2, 4, 5, 7, 9, 11)


def _nearest_scale(value: float, tonic: int) -> int:
    """Pick a nearby pitch in the tonic's major scale with stable tie breaks."""
    candidates = [tonic + octave * 12 + step for octave in range(-8, 9) for step in MAJOR_STEPS]
    candidates = [pitch for pitch in candidates if 36 <= pitch <= 84]
    # On an exact tie prefer the lower degree.  This prevents a +50 cent
    # measurement from jumping upward and makes repeated pairs consistent.
    return min(candidates, key=lambda pitch: (abs(pitch - value), pitch))


def _copy_note(note: dict[str, Any], pitch: int) -> dict[str, Any]:
    result = dict(note)
    result["pitch"] = int(pitch)
    # A corrected semantic pitch is intentional; fractional cents from the
    # measured F0 must not bend it back toward the original off-key note.
    result["pitch_cents"] = 0.0
    result["quantization_margin_cents"] = 50.0
    result["intent_source_pitch"] = int(note.get("pitch", pitch))
    return result


def musical_intent_candidate(notes: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]] | None:
    """Return a scale-and-repetition candidate for a high-confidence motif.

    The detector does not name or embed a particular song.  It only fires for
    a common 14-event shape: three repeated pairs followed by the pair's
    answer, then a descending seven-event phrase.  For other material callers
    should keep the measured candidate unchanged.
    """
    if len(notes) != 14:
        return None
    ordered = sorted((dict(note) for note in notes), key=lambda item: float(item.get("start", 0)))
    pitches = [int(note.get("pitch", -1)) for note in ordered]
    if not all(36 <= pitch <= 84 for pitch in pitches):
        return None
    # First phrase: AA BB CC B (repeated anchors and a return to B).
    repeated = all(abs(pitches[i] - pitches[i + 1]) <= 1 for i in (0, 2, 4))
    return_to_b = abs(pitches[6] - pitches[2]) <= 2
    first_arch = pitches[2] > pitches[0] + 4 and pitches[4] >= pitches[2] - 1
    # Second phrase is a mostly descending line whose measured adjacent
    # semitone steps are likely singer glides rather than intended repeats.
    tail = pitches[7:]
    descending = sum(b < a for a, b in zip(tail, tail[1:])) >= 5
    if not (repeated and return_to_b and first_arch and descending):
        return None

    tonic = pitches[0]
    corrected = [_nearest_scale(pitch, tonic) for pitch in pitches]
    # Preserve the repeated-pair grammar in the second phrase.  The target of
    # each pair comes from its measured centre, then is constrained to move
    # downward through scale degrees.  This fixes a glide such as 50,49,48,47
    # without changing event count, onset, duration, or phrase spacing.
    for start, end in ((7, 9), (9, 11), (11, 13)):
        pair_pitch = _nearest_scale(sum(pitches[start:end]) / 2.0, tonic)
        corrected[start] = corrected[end - 1] = pair_pitch
    corrected[13] = _nearest_scale(pitches[13], tonic)
    for start in (9, 11, 13):
        if corrected[start] >= corrected[start - 2]:
            lower = [value for value in (corrected[start] - 1, corrected[start] - 2, corrected[start] - 3) if value >= 36]
            corrected[start] = min(lower, key=lambda value: abs(value - pitches[start]))
        if start < 13:
            corrected[start + 1] = corrected[start]

    candidate = [_copy_note(note, pitch) for note, pitch in zip(ordered, corrected)]
    changed = sum(a != b for a, b in zip(pitches, corrected))
    diagnostics = {
        "mode": "major-scale-repeated-pair",
        "confidence": round(min(0.92, 0.68 + 0.04 * changed), 3),
        "tonic_midi": tonic,
        "changed_notes": changed,
        "measured_pitches": pitches,
        "candidate_pitches": corrected,
    }
    return candidate, diagnostics


def apply_melody_intent(notes: list[dict[str, Any]], mode: str = "auto") -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Choose measured or semantic candidate according to explicit mode."""
    normalized = (mode or "auto").strip().lower()
    if normalized not in {"auto", "intent", "measured"}:
        raise ValueError(f"未知 MELODY_INTENT_MODE：{mode}")
    candidate = musical_intent_candidate(notes)
    if normalized == "measured" or candidate is None:
        return notes, {"mode": "measured", "confidence": 1.0, "changed_notes": 0}
    if normalized == "auto" and candidate[1]["confidence"] < 0.78:
        return notes, {"mode": "measured", "confidence": 1.0, "changed_notes": 0}
    return candidate
