import tempfile
import unittest
from pathlib import Path
import pysubs2

from cartoon_sub.subtitle.models import Utterance, SubtitleStyle, Mask, Project, DisplaySegment
from cartoon_sub.speaker.service import detect_overlaps, resolve_subtitle_lanes, refresh_timeline
from cartoon_sub.subtitle.renderer import save_ass
from cartoon_sub.tts.mix_service import TTSTimelineMixService


class PipelineTimingAlignmentTests(unittest.TestCase):
    def test_legitimate_overlap_requires_distinct_speakers(self):
        # Different speakers overlapping -> Legitimate overlap
        diff_spk = [
            Utterance(1, 1.0, 4.0, "你好", vi="Chào bạn", speaker_id="SPK_01", speaker_name="Alice"),
            Utterance(2, 2.0, 5.0, "早安", vi="Chào buổi sáng", speaker_id="SPK_02", speaker_name="Bob"),
        ]
        detect_overlaps(diff_spk)
        self.assertTrue(diff_spk[0].overlap)
        self.assertTrue(diff_spk[1].overlap)
        self.assertEqual(diff_spk[0].overlap_group, "OVL_001")
        self.assertEqual(diff_spk[1].overlap_group, "OVL_001")

        # Same speaker overlapping -> NOT legitimate dialogue overlap (collision/overflow)
        same_spk = [
            Utterance(1, 1.0, 4.0, "你好", vi="Chào bạn", speaker_id="SPK_01", speaker_name="Alice"),
            Utterance(2, 2.0, 5.0, "早安", vi="Chào buổi sáng", speaker_id="SPK_01", speaker_name="Alice"),
        ]
        detect_overlaps(same_spk)
        self.assertFalse(same_spk[0].overlap)
        self.assertFalse(same_spk[1].overlap)
        self.assertIsNone(same_spk[0].overlap_group)
        self.assertIsNone(same_spk[1].overlap_group)

    def test_multi_lane_presentation_and_stable_lane_retention(self):
        # 3 utterances in overlap group:
        # SPK_01 starts at 1.0 -> Lane 0
        # SPK_02 starts at 2.0 -> Lane 1
        # SPK_01 speaks again at 2.5 -> stays Lane 0
        # Single non-overlap utterance 4 at 10.0 -> Lane 0
        utterances = [
            Utterance(1, 1.0, 3.0, "你好", vi="Xin chào", speaker_id="SPK_01", speaker_name="Alice"),
            Utterance(2, 2.0, 4.0, "早安", vi="Chào buổi sáng", speaker_id="SPK_02", speaker_name="Bob"),
            Utterance(3, 2.5, 5.0, "再见", vi="Tạm biệt", speaker_id="SPK_01", speaker_name="Alice"),
            Utterance(4, 10.0, 12.0, "好的", vi="Được rồi", speaker_id="SPK_01", speaker_name="Alice"),
        ]
        detect_overlaps(utterances)
        lanes = resolve_subtitle_lanes(utterances)

        self.assertEqual(lanes[1], 0)  # SPK_01 first in group -> Lane 0
        self.assertEqual(lanes[2], 1)  # SPK_02 second in group -> Lane 1
        self.assertEqual(lanes[3], 0)  # SPK_01 retains Lane 0 (no lane swapping!)
        self.assertEqual(lanes[4], 0)  # non-overlap -> Lane 0

    def test_ass_rendering_multi_lane_positions_in_mask(self):
        u1 = Utterance(1, 1.0, 4.0, "中文1", vi="Câu một", speaker_id="SPK_01", speaker_name="Alice")
        u2 = Utterance(2, 2.0, 5.0, "中文2", vi="Câu hai", speaker_id="SPK_02", speaker_name="Bob")
        u3 = Utterance(3, 10.0, 12.0, "中文3", vi="Câu ba", speaker_id="SPK_01", speaker_name="Alice")
        project = Project(
            "test_proj", "video.mp4",
            metadata={"width": 1280, "height": 720, "duration": 20.0},
            segments=[u1, u2, u3],
            mask=Mask(enabled=True, kind="solid", x=100, y=500, width=1080, height=160),
            subtitle_style=SubtitleStyle(font_size=30, center_in_mask=True, speaker_label_mode="overlap_only")
        )
        refresh_timeline(project)

        with tempfile.TemporaryDirectory() as tmp:
            ass_path = Path(tmp) / "test.ass"
            save_ass(project, ass_path)
            subs = pysubs2.load(str(ass_path))

        # First two overlapping segments must have DIFFERENT pos tags so they don't draw over each other
        event1 = next(e for e in subs if "Câu một" in e.text)
        event2 = next(e for e in subs if "Câu hai" in e.text)
        event3 = next(e for e in subs if "Câu ba" in e.text)

        self.assertIn(r"\pos(", event1.text)
        self.assertIn(r"\pos(", event2.text)
        # Verify event1 and event2 have distinct Y positions
        self.assertNotEqual(event1.text[:event1.text.find("}")], event2.text[:event2.text.find("}")])
        # Non-overlapping event3 should be centered in mask (mask center Y is 500 + 80 = 580, center X is 100 + 540 = 640)
        self.assertIn(r"{\an5\pos(640,580)}", event3.text)

    def test_speaker_label_modes(self):
        u1 = Utterance(1, 1.0, 4.0, "中文1", vi="Xin chào", speaker_id="SPK_01", speaker_name="Alice")
        u2 = Utterance(2, 2.0, 5.0, "中文2", vi="Tôi là Bob", speaker_id="SPK_02", speaker_name="Unknown")
        u3 = Utterance(3, 10.0, 12.0, "中文3", vi="Câu đơn", speaker_id="SPK_01", speaker_name="Alice")

        def make_proj(mode):
            p = Project(
                "label_proj", "video.mp4",
                metadata={"width": 1280, "height": 720, "duration": 20.0},
                segments=[u1, u2, u3],
                subtitle_style=SubtitleStyle(font_size=30, speaker_label_mode=mode)
            )
            refresh_timeline(p)
            return p

        with tempfile.TemporaryDirectory() as tmp:
            # 1. OVERLAP_ONLY
            p_overlap = make_proj("overlap_only")
            save_ass(p_overlap, Path(tmp) / "overlap.ass")
            subs = pysubs2.load(str(Path(tmp) / "overlap.ass"))
            e1 = next(e for e in subs if "Xin chào" in e.text)
            e2 = next(e for e in subs if "Tôi là Bob" in e.text)
            e3 = next(e for e in subs if "Câu đơn" in e.text)

            self.assertIn("Alice: Xin chào", e1.text)
            # Unknown speaker displays SPK_02 instead of "Unknown:"
            self.assertIn("SPK_02: Tôi là Bob", e2.text)
            self.assertNotIn("Alice:", e3.text)  # Non-overlap has NO prefix in overlap_only mode

            # TTS dubbing remains completely untouched
            self.assertEqual(u1.vi_dubbing, "Xin chào")
            self.assertEqual(u2.vi_dubbing, "Tôi là Bob")

            # 2. OFF
            p_off = make_proj("off")
            save_ass(p_off, Path(tmp) / "off.ass")
            subs_off = pysubs2.load(str(Path(tmp) / "off.ass"))
            for e in subs_off:
                self.assertNotIn("Alice:", e.text)
                self.assertNotIn("SPK_02:", e.text)

            # 3. ALWAYS
            p_always = make_proj("always")
            save_ass(p_always, Path(tmp) / "always.ass")
            subs_always = pysubs2.load(str(Path(tmp) / "always.ass"))
            e3_always = next(e for e in subs_always if "Câu đơn" in e.text)
            self.assertIn("Alice: Câu đơn", e3_always.text)

            # 4. DEBUG
            p_debug = make_proj("debug")
            save_ass(p_debug, Path(tmp) / "debug.ass")
            subs_debug = pysubs2.load(str(Path(tmp) / "debug.ass"))
            e1_debug = next(e for e in subs_debug if "Xin chào" in e.text)
            self.assertIn("[SPK_01|1.00-4.00]", e1_debug.text)

    def test_tts_alignment_diagnostics_and_no_drift(self):
        # Slot duration = 2.0s
        # 1. WAV duration 1.9s -> SYNC_OK
        u_ok = Utterance(1, 10.0, 12.0, "中文", vi="Khớp thời gian", tts_duration=1.9)
        self.assertEqual(u_ok.tts_alignment_diagnostic, "SYNC_OK")
        self.assertEqual(u_ok.tts_alignment_status, "fits")

        # 2. WAV duration 2.3s -> ratio 1.15 <= 1.20 -> AUTO_FIT
        u_fit = Utterance(2, 10.0, 12.0, "中文", vi="Tràn nhẹ", tts_duration=2.3)
        self.assertEqual(u_fit.tts_alignment_diagnostic, "AUTO_FIT")
        self.assertEqual(u_fit.tts_alignment_status, "warning")

        # 3. WAV duration 2.8s -> ratio 1.40 > 1.20 -> NEEDS_TIMING_REVIEW
        u_review = Utterance(3, 10.0, 12.0, "中文", vi="Tràn nặng", tts_duration=2.8)
        self.assertEqual(u_review.tts_alignment_diagnostic, "NEEDS_TIMING_REVIEW")
        self.assertEqual(u_review.tts_alignment_status, "warning")

        # Master timestamps must never be modified
        self.assertEqual(u_fit.start, 10.0)
        self.assertEqual(u_fit.end, 12.0)
        self.assertEqual(u_review.start, 10.0)
        self.assertEqual(u_review.end, 12.0)


if __name__ == "__main__":
    unittest.main()
