import unittest

from app.ir import midi_summary
from app.midi_style import anchor_style_plan, arrangement_to_midi, validate_arrangement


class MidiStyleTests(unittest.TestCase):
    def test_style_model_may_use_multiple_tracks_but_preserves_source_events(self):
        ir = {
            "tempo_bpm": 100,
            "note_events": [
                {"pitch": 60, "start": 0.0, "duration": 0.4, "velocity": 88},
                {"pitch": 62, "start": 0.5, "duration": 0.4, "velocity": 88},
            ],
        }
        plan = validate_arrangement(
            {"tempo_bpm": 100, "tracks": [
                {"name": "funk_guitar", "program": 27, "notes": [
                    {"pitch": 60, "start": 0.0, "duration": 0.4, "velocity": 70},
                ]},
                {"name": "funk_keys", "program": 4, "notes": [
                    {"pitch": 62, "start": 0.5, "duration": 0.4, "velocity": 74},
                ]},
                {"name": "drums", "program": 0, "notes": [{"pitch": 36, "start": 0, "duration": 0.1}]},
            ]},
            ir,
        )
        self.assertEqual([note["pitch"] for track in plan["tracks"][:2] for note in track["notes"]], [60, 62])
        self.assertEqual([note["start"] for track in plan["tracks"][:2] for note in track["notes"]], [0.0, 0.5])
        self.assertEqual([note["duration"] for track in plan["tracks"][:2] for note in track["notes"]], [0.4, 0.4])
        self.assertEqual(plan["tracks"][0]["program"], 27)

    def test_style_serializes_multiple_tracks(self):
        plan = {
            "tempo_bpm": 100,
            "tracks": [
                {"name": "funk_guitar", "program": 27, "notes": [{"pitch": 60, "start": 0, "duration": 0.4, "velocity": 88}]},
                {"name": "bass", "program": 33, "notes": [{"pitch": 48, "start": 0, "duration": 0.4, "velocity": 72}]},
            ],
        }
        summary = midi_summary(arrangement_to_midi(plan))
        self.assertEqual(len(summary["note_events"]), 2)
        self.assertEqual({note["pitch"] for note in summary["note_events"]}, {48, 60})

    def test_style_model_can_rewrite_pitch_and_rhythm(self):
        ir = {"tempo_bpm": 100, "note_events": [
            {"pitch": 60, "start": 0.0, "duration": 0.4, "velocity": 88},
        ]}
        plan = validate_arrangement({"tempo_bpm": 104, "tracks": [{"name": "funk_keys", "program": 4, "notes": [
            {"pitch": 64, "start": 0.02, "duration": 0.32, "velocity": 96},
            {"pitch": 67, "start": 0.34, "duration": 0.32, "velocity": 84},
        ]}]}, ir)
        self.assertEqual(plan["tempo_bpm"], 104)
        self.assertEqual([n["pitch"] for n in plan["tracks"][0]["notes"]], [64, 67])

    def test_style_anchor_restores_source_onsets_and_phrase_pause(self):
        ir = {"note_events": [
            {"pitch": 60, "start": 1.0, "duration": 0.4},
            {"pitch": 62, "start": 1.5, "duration": 0.4},
            {"pitch": 64, "start": 3.0, "duration": 0.4},
        ]}
        plan = anchor_style_plan({"tempo_bpm": 100, "tracks": [
            {"name": "lead_reimagined", "notes": [
                {"pitch": 70, "start": 0.0, "duration": 0.2},
                {"pitch": 72, "start": 0.4, "duration": 0.2},
                {"pitch": 74, "start": 0.8, "duration": 0.2},
            ]},
            {"name": "bass", "notes": [
                {"pitch": 40, "start": 0.0, "duration": 1.0},
                {"pitch": 42, "start": 0.8, "duration": 0.8},
            ]},
        ]}, ir, "funk")
        lead = plan["tracks"][0]["notes"]
        self.assertEqual([note["start"] for note in lead], [1.0, 1.5, 3.0])
        self.assertEqual([note["duration"] for note in lead], [0.4, 0.4, 0.4])
        # The backing note is clipped before the measured 1.9–3.0s pause.
        bass = plan["tracks"][1]["notes"]
        self.assertTrue(all(note["start"] + note["duration"] <= 1.9 + 1e-4 or note["start"] >= 3.0 for note in bass))

    def test_style_anchor_does_not_leave_byte_for_byte_lead_copy(self):
        ir = {"tempo_bpm": 100, "note_events": [
            {"pitch": 60 + (index % 4), "start": index * 0.5, "duration": 0.4}
            for index in range(8)
        ]}
        plan = anchor_style_plan({"tempo_bpm": 100, "tracks": [{"name": "lead", "notes": [
            {"pitch": note["pitch"], "start": note["start"], "duration": note["duration"]}
            for note in ir["note_events"]
        ]}]}, ir, "funk")
        self.assertNotEqual([n["pitch"] for n in plan["tracks"][0]["notes"]], [n["pitch"] for n in ir["note_events"]])
        self.assertEqual([n["start"] for n in plan["tracks"][0]["notes"]], [n["start"] for n in ir["note_events"]])

    def test_funk_backbeat_adds_snare_without_filling_phrase_pause(self):
        ir = {"tempo_bpm": 100, "note_events": [
            {"pitch": 60, "start": 1.0, "duration": 0.4},
            {"pitch": 62, "start": 1.5, "duration": 0.4},
            {"pitch": 64, "start": 3.0, "duration": 0.4},
            {"pitch": 65, "start": 3.5, "duration": 0.4},
            {"pitch": 64, "start": 4.0, "duration": 0.4},
            {"pitch": 62, "start": 4.5, "duration": 0.4},
            {"pitch": 60, "start": 6.0, "duration": 0.4},
            {"pitch": 59, "start": 6.5, "duration": 0.4},
        ]}
        plan = anchor_style_plan({"tracks": [{"name": "lead", "notes": ir["note_events"]}, {"name": "drums", "notes": []}]}, ir, "funk")
        drum = next(track for track in plan["tracks"] if track["name"] == "drums")
        self.assertIn(38, {note["pitch"] for note in drum["notes"]})
        self.assertFalse(any(5.0 < note["start"] < 6.0 for note in drum["notes"]))

    def test_lofi_long_texture_is_clipped_at_phrase_pause(self):
        ir = {"tempo_bpm": 100, "note_events": [
            {"pitch": 60, "start": 1.0, "duration": 0.4},
            {"pitch": 62, "start": 1.5, "duration": 0.4},
            {"pitch": 64, "start": 3.0, "duration": 0.4},
            {"pitch": 65, "start": 3.5, "duration": 0.4},
            {"pitch": 64, "start": 4.0, "duration": 0.4},
            {"pitch": 62, "start": 4.5, "duration": 0.4},
            {"pitch": 60, "start": 6.0, "duration": 0.4},
            {"pitch": 59, "start": 6.5, "duration": 0.4},
        ]}
        plan = anchor_style_plan({"tracks": [
            {"name": "lead", "notes": ir["note_events"]},
            {"name": "harmony", "notes": [
                {"pitch": 60, "start": index * 0.5, "duration": 0.2}
                for index in range(16)
            ]},
        ]}, ir, "lofi")
        harmony = next(track for track in plan["tracks"] if track["name"] == "harmony")
        self.assertFalse(any(note["start"] < 5.0 and note["start"] + note["duration"] > 5.0 for note in harmony["notes"]))


if __name__ == "__main__":
    unittest.main()
