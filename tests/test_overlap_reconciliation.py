import tempfile
import unittest
from pathlib import Path

from cartoon_sub.project.project_manager import ProjectManager
from cartoon_sub.speaker.service import (
    MIN_RECONCILED_DURATION,
    approve_review,
    detect_overlaps,
    duplicate_continuation_candidate,
    reconcile_overlaps,
)
from cartoon_sub.subtitle.models import DisplaySegment, Project, Segment
from cartoon_sub.tts.mix_service import TTSTimelineMixService


class OverlapReconciliationTests(unittest.TestCase):
    def test_same_speaker_overlap_clamps_previous_end(self):
        rows = [
            Segment(17, 74.2, 76.5, "我要送孩子", speaker_id="SPK_01"),
            Segment(18, 75.0, 77.5, "我要送孩子去补习班", speaker_id="SPK_01"),
        ]
        summary = reconcile_overlaps(rows)
        self.assertEqual(rows[0].end, 75.0)
        self.assertFalse(any(row.overlap for row in rows))
        self.assertTrue(all(row.overlap_type == "SAME_SPEAKER_CONFLICT" for row in rows))
        self.assertIn("DUPLICATE_CONTINUATION", rows[0].overlap_diagnostics)
        self.assertEqual(summary["same_speaker_fixed"], 1)

    def test_reconcile_resizes_existing_display_timing_and_roundtrips(self):
        first = Segment(1, 1, 4, "我要送孩子", vi="Tôi đưa con đi học", speaker_id="SPK_01")
        first.set_display_segments([
            DisplaySegment("1.1", 1, 1, 2.5, "Tôi đưa con", manual=True),
            DisplaySegment("1.2", 1, 2.5, 4, "đi học", manual=True),
        ])
        second = Segment(2, 3, 5, "我要送孩子去补习班", vi="Tôi đưa con đi học thêm", speaker_id="SPK_01")
        project = Project("p", "missing.mp4", segments=[first, second])
        reconcile_overlaps(project.utterances)
        self.assertEqual(first.end, 3)
        self.assertEqual(first.display_segments[-1].end, 3)
        loaded = Project.from_dict(project.to_dict())
        self.assertEqual(loaded.utterances[0].display_segments[-1].end, 3)
        self.assertIn("CLAMP_PREVIOUS_END", loaded.utterances[0].overlap_diagnostics)

    def test_speaker_approval_reconciles_and_marks_old_tts_stale(self):
        rows = [
            Segment(1, 1, 4, "甲", speaker_id="SPK_01", tts_generation_status="generated"),
            Segment(2, 2, 5, "乙", speaker_id="SPK_01"),
        ]
        project = Project("p", "missing.mp4", segments=rows)
        approve_review(project)
        self.assertEqual(rows[0].end, 2)
        self.assertEqual(rows[0].tts_generation_status, "stale")
        self.assertEqual(project.final_audio_status, "stale")

    def test_same_speaker_non_overlap_is_unchanged(self):
        rows = [Segment(1, 1, 2, "甲", speaker_id="SPK_01"),
                Segment(2, 2, 3, "乙", speaker_id="SPK_01")]
        before = [(row.start, row.end) for row in rows]
        reconcile_overlaps(rows)
        self.assertEqual([(row.start, row.end) for row in rows], before)
        self.assertTrue(all(row.overlap_type == "NONE" for row in rows))

    def test_different_speaker_overlap_is_legitimate_and_unchanged(self):
        rows = [Segment(1, 10, 14, "甲", speaker_id="SPK_01"),
                Segment(2, 12, 16, "乙", speaker_id="SPK_02")]
        before = [(row.start, row.end) for row in rows]
        summary = reconcile_overlaps(rows)
        self.assertEqual([(row.start, row.end) for row in rows], before)
        self.assertTrue(all(row.overlap for row in rows))
        self.assertTrue(all(row.overlap_type == "LEGITIMATE_OVERLAP" for row in rows))
        self.assertEqual(summary["legitimate"], 1)

    def test_unknown_speaker_overlap_requires_review_without_clamp(self):
        rows = [Segment(1, 1, 4, "甲"), Segment(2, 2, 5, "乙", speaker_id="SPK_02")]
        reconcile_overlaps(rows)
        self.assertEqual(rows[0].end, 4)
        self.assertFalse(any(row.overlap for row in rows))
        self.assertTrue(all(row.overlap_type == "UNKNOWN_SPEAKER_REVIEW" for row in rows))

    def test_too_short_clamp_is_rejected(self):
        rows = [Segment(1, 1, 2, "甲", speaker_id="SPK_01"),
                Segment(2, 1 + MIN_RECONCILED_DURATION / 2, 3, "乙", speaker_id="SPK_01")]
        summary = reconcile_overlaps(rows)
        self.assertEqual(rows[0].end, 2)
        self.assertEqual(summary["timing_review"], 1)
        self.assertTrue(all(row.overlap_type == "TIMING_REVIEW_REQUIRED" for row in rows))

    def test_duplicate_continuation_heuristic(self):
        self.assertTrue(duplicate_continuation_candidate("我要送孩子。", "我要送孩子去补习班"))
        self.assertFalse(duplicate_continuation_candidate("天气很好", "马上回家"))

    def test_tts_guard_rejects_unreconciled_same_speaker_overlap(self):
        project = Project("p", "missing.mp4", metadata={"duration": 10.0}, segments=[
            Segment(1, 1, 4, "甲", speaker_id="SPK_01"),
            Segment(2, 2, 5, "乙", speaker_id="SPK_01"),
        ])
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "FALSE_OVERLAP_SAFETY"):
                TTSTimelineMixService().mix(project, directory, "http://127.0.0.1:8765")

    def test_old_project_without_overlap_metadata_loads(self):
        project = Project("p", "missing.mp4", segments=[Segment(1, 1, 2, "旧数据")])
        data = project.to_dict()
        data["master_timeline"][0].pop("overlap_type", None)
        data["master_timeline"][0].pop("overlap_diagnostics", None)
        loaded = Project.from_dict(data)
        self.assertEqual(loaded.utterances[0].overlap_type, "NONE")
        self.assertEqual(loaded.utterances[0].overlap_diagnostics, [])


if __name__ == "__main__":
    unittest.main()
