import tempfile
import unittest
from pathlib import Path
from PySide6.QtWidgets import QApplication

from cartoon_sub.subtitle.models import Utterance, Project, SubtitleStyle
from cartoon_sub.speaker.service import approve_review, refresh_timeline
from cartoon_sub.app.controller import Controller
from cartoon_sub.project.project_manager import ProjectManager
from cartoon_sub.subtitle.segmentation_service import SubtitleSegmentationService
from cartoon_sub.ui.timeline_table import create_table, populate, get_dirty_rows, EDITABLE_COLUMNS


# Ensure single QApplication instance for Qt widget tests
_app = QApplication.instance() or QApplication([])


class ManualEditsTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.project_dir = Path(self.tmp_dir.name)
        self.video_path = self.project_dir / "test.mp4"
        self.video_path.write_bytes(b"dummy")

        u1 = Utterance(1, 10.0, 14.0, "你好", vi="Xin chào", vi_subtitle="Xin chào", vi_dubbing="Xin chào",
                       speaker_id="SPK_01", speaker_name="Alice", tts_generation_status="generated")
        u2 = Utterance(2, 13.0, 16.0, "早安", vi="Chào buổi sáng", vi_subtitle="Chào buổi sáng", vi_dubbing="Chào buổi sáng",
                       speaker_id="SPK_02", speaker_name="Bob", tts_generation_status="generated")
        u3 = Utterance(3, 20.0, 22.0, "再见", vi="Tạm biệt", vi_subtitle="Tạm biệt", vi_dubbing="Tạm biệt",
                       speaker_id="SPK_01", speaker_name="Alice", tts_generation_status="generated")

        self.project = Project("test_project", str(self.video_path), segments=[u1, u2, u3])
        approve_review(self.project)
        ProjectManager().save(self.project, self.project_dir)

        self.controller = Controller()
        self.controller.accept((self.project, self.project_dir))

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_dirty_tracking_in_timeline_table(self):
        table = create_table()
        dirty_counts = []
        table.on_dirty_changed = lambda c: dirty_counts.append(c)

        populate(table, self.project)
        self.assertEqual(len(table.dirty_rows), 0)

        # Non-editable column (ID: col 0)
        from PySide6.QtCore import Qt
        self.assertFalse(bool(table.item(0, 0).flags() & Qt.ItemFlag.ItemIsEditable))
        # Editable column (VI Subtitle: col 7)
        self.assertTrue(bool(table.item(0, 7).flags() & Qt.ItemFlag.ItemIsEditable))

        # Edit cell directly in table
        table.item(0, 7).setText("Xin chào sửa tay")
        self.assertIn(1, table.dirty_rows)
        self.assertEqual(len(table.dirty_rows), 1)
        self.assertGreater(len(dirty_counts), 0)
        self.assertEqual(dirty_counts[-1], 1)

        dirty_data = get_dirty_rows(table)
        self.assertEqual(len(dirty_data), 1)
        self.assertEqual(dirty_data[0]["id"], 1)
        self.assertEqual(dirty_data[0]["vi_subtitle"], "Xin chào sửa tay")

        # Restore original value
        table.item(0, 7).setText("Xin chào")
        self.assertEqual(len(table.dirty_rows), 0)
        self.assertEqual(dirty_counts[-1], 0)

    def test_apply_manual_edits_vi_subtitle_downstream(self):
        # Case 0: Single display_segment gets its vi_text directly updated to new vi_subtitle
        self.project.segments[0].set_display_segments([
            {"id": "1.1", "utterance_id": 1, "start": 10.0, "end": 14.0, "vi_text": "Xin chào cũ", "segmentation_reason": "test"}
        ])
        self.assertEqual(len(self.project.segments[0].display_segments), 1)

        dirty_rows = [{
            "id": 1, "row_index": 1, "start": "10.000", "end": "14.000",
            "speaker": "SPK_01", "zh": "你好", "vi_subtitle": "Câu phụ đề mới", "vi_dubbing": "Xin chào"
        }]

        self.controller.apply_manual_edits(dirty_rows)

        u1 = self.project.segments[0]
        self.assertEqual(u1.vi_subtitle, "Câu phụ đề mới")
        # Display segment updated to new vi_subtitle directly (Case 0)
        self.assertEqual(len(u1.display_segments), 1)
        self.assertEqual(u1.display_segments[0].vi_text, "Câu phụ đề mới")
        self.assertEqual(u1.display_segments[0].start, 10.0)
        self.assertEqual(u1.display_segments[0].end, 14.0)
        # VI Dubbing was not modified -> TTS is NOT marked stale
        self.assertEqual(u1.tts_generation_status, "generated")

        # Persistence check
        loaded = ProjectManager().load(self.project_dir)
        self.assertEqual(loaded.segments[0].vi_subtitle, "Câu phụ đề mới")
        self.assertEqual(loaded.segments[0].display_segments[0].vi_text, "Câu phụ đề mới")

    def test_apply_manual_edits_case_a_auto_segmented(self):
        # Case A: Auto-derived DisplaySegments (len > 1, manual=False)
        long_text = "Haiz, thôi bỏ đi, chim gián tử thì cứ là chim gián tử vậy. Ngụy Trưng đúng là một dòng nước trong sạch, vừa có công tòng long, lại vừa có công huấn long. Cứ tùy tiện thăng chức cho hắn đi."
        u1 = self.project.segments[0]
        u1.start, u1.end = 10.0, 18.0
        u1.vi_subtitle = long_text
        self.controller.segmentation_service.auto_segment(self.project, [1], force=True)
        self.assertGreater(len(u1.display_segments), 1)
        self.assertFalse(any(s.manual for s in u1.display_segments))

        # User edits vi_subtitle
        new_text = "Thôi bỏ đi! Ngụy Trưng quả là trung thần ngay thẳng, lập nhiều công lớn. Mau thăng chức cho ông ấy!"
        dirty_rows = [{
            "id": 1, "row_index": 1, "start": "10.000", "end": "18.000",
            "speaker": "SPK_01", "zh": "你好", "vi_subtitle": new_text, "vi_dubbing": "Xin chào"
        }]
        self.controller.apply_manual_edits(dirty_rows)

        # Verified re-segmented locally without Gemini, reflecting new text
        self.assertEqual(u1.vi_subtitle, new_text)
        reconstructed = " ".join(s.vi_text for s in u1.display_segments)
        self.assertIn("Thôi bỏ đi", reconstructed)
        self.assertIn("thăng chức", reconstructed)

        # Persistence check
        loaded = ProjectManager().load(self.project_dir)
        self.assertEqual(loaded.segments[0].vi_subtitle, new_text)
        self.assertEqual(len(loaded.segments[0].display_segments), len(u1.display_segments))

    def test_apply_manual_edits_case_b_manually_timed_segments_preserved(self):
        # Case B: Manually timed DisplaySegments (manual=True)
        # Segment boundaries set by user at exact timestamps
        u1 = self.project.segments[0]
        u1.start, u1.end = 10.0, 18.0
        u1.set_display_segments([
            {"id": "1.1", "utterance_id": 1, "start": 10.0, "end": 12.5, "vi_text": "Cắt đoạn 1 cũ", "manual": True, "segmentation_reason": "manual"},
            {"id": "1.2", "utterance_id": 1, "start": 12.5, "end": 15.0, "vi_text": "Cắt đoạn 2 cũ", "manual": True, "segmentation_reason": "manual"},
            {"id": "1.3", "utterance_id": 1, "start": 15.0, "end": 18.0, "vi_text": "Cắt đoạn 3 cũ", "manual": True, "segmentation_reason": "manual"},
        ])
        self.project.segmentation_cache["1"] = {"manual": True, "timing_source": "manual"}
        self.assertEqual(len(u1.display_segments), 3)

        new_text = "Than ôi, thôi bỏ đi, chim sẻ thì cứ là chim sẻ vậy. Ngụy Trưng quả thực là một trung thần chí công vô tư. Cứ thăng chức cho ông ấy đi."
        dirty_rows = [{
            "id": 1, "row_index": 1, "start": "10.000", "end": "18.000",
            "speaker": "SPK_01", "zh": "你好", "vi_subtitle": new_text, "vi_dubbing": "Xin chào"
        }]

        self.controller.apply_manual_edits(dirty_rows)

        # Timestamps MUST BE EXACTLY PRESERVED
        self.assertEqual(len(u1.display_segments), 3)
        self.assertAlmostEqual(u1.display_segments[0].start, 10.0)
        self.assertAlmostEqual(u1.display_segments[0].end, 12.5)
        self.assertAlmostEqual(u1.display_segments[1].start, 12.5)
        self.assertAlmostEqual(u1.display_segments[1].end, 15.0)
        self.assertAlmostEqual(u1.display_segments[2].start, 15.0)
        self.assertAlmostEqual(u1.display_segments[2].end, 18.0)

        # All parts must contain new reflowed text and be manual
        self.assertTrue(all(s.manual for s in u1.display_segments))
        for seg in u1.display_segments:
            self.assertTrue(len(seg.vi_text.strip()) > 0)
            self.assertNotIn("cũ", seg.vi_text)

        # Verify state is manual in cache
        self.assertTrue(self.project.segmentation_cache["1"]["manual"])

        # Persistence check
        loaded = ProjectManager().load(self.project_dir)
        self.assertEqual(len(loaded.segments[0].display_segments), 3)
        self.assertAlmostEqual(loaded.segments[0].display_segments[0].start, 10.0)
        self.assertAlmostEqual(loaded.segments[0].display_segments[0].end, 12.5)
        self.assertAlmostEqual(loaded.segments[0].display_segments[1].start, 12.5)
        self.assertAlmostEqual(loaded.segments[0].display_segments[1].end, 15.0)
        self.assertAlmostEqual(loaded.segments[0].display_segments[2].start, 15.0)
        self.assertAlmostEqual(loaded.segments[0].display_segments[2].end, 18.0)
        self.assertTrue(all(s.manual for s in loaded.segments[0].display_segments))

    def test_apply_manual_edits_vi_dubbing_downstream(self):
        dirty_rows = [{
            "id": 1, "row_index": 1, "start": "10.000", "end": "14.000",
            "speaker": "SPK_01", "zh": "你好", "vi_subtitle": "Xin chào", "vi_dubbing": "Thoại lồng tiếng mới"
        }]

        self.controller.apply_manual_edits(dirty_rows)

        u1 = self.project.segments[0]
        self.assertEqual(u1.vi_dubbing, "Thoại lồng tiếng mới")
        # VI Dubbing modified -> TTS becomes stale
        self.assertEqual(u1.tts_generation_status, "stale")
        self.assertEqual(self.project.final_audio_status, "stale")

    def test_subtitle_refresh_syncs_stale_persisted_segments_after_reload(self):
        u1 = self.project.segments[0]
        u1.set_display_segments([
            {"id": "1.1", "utterance_id": 1, "start": 10.0, "end": 14.0,
             "vi_text": "Câu cũ", "segmentation_reason": "local"}
        ])
        u1.vi_subtitle = "Câu mới từ master"
        ProjectManager().save(self.project, self.project_dir)

        loaded = ProjectManager().load(self.project_dir)
        rows = SubtitleSegmentationService().rows(loaded)
        self.assertEqual(rows[0][1][0][0].vi_text, "Câu mới từ master")
        self.assertEqual(loaded.segments[0].display_segments[0].vi_text, "Câu mới từ master")

    def test_dialog_edit_reflows_manual_timing_without_touching_overlap_peer(self):
        u1, u2 = self.project.segments[:2]
        u1.set_display_segments([
            {"id": "1.1", "utterance_id": 1, "start": 10.0, "end": 12.0,
             "vi_text": "Cũ một", "manual": True, "segmentation_reason": "manual"},
            {"id": "1.2", "utterance_id": 1, "start": 12.0, "end": 14.0,
             "vi_text": "Cũ hai", "manual": True, "segmentation_reason": "manual"},
        ])
        self.project.segmentation_cache["1"] = {"manual": True, "timing_source": "manual"}
        peer_timing = (u2.start, u2.end, u2.speaker_id)

        self.controller.edit_utterance(1, "Nội dung master mới đủ dài để chia vào hai ô", "Xin chào", "balanced_dubbing", None)

        self.assertEqual([(s.start, s.end) for s in u1.display_segments], [(10.0, 12.0), (12.0, 14.0)])
        self.assertNotIn("Cũ", " ".join(s.vi_text for s in u1.display_segments))
        self.assertEqual((u2.start, u2.end, u2.speaker_id), peer_timing)

    def test_apply_manual_edits_timing_and_overlap(self):
        # u1: 10->14 (SPK_01), u2: 13->16 (SPK_02) -> legitimately overlap
        refresh_timeline(self.project)
        self.assertTrue(self.project.segments[0].overlap)
        self.assertTrue(self.project.segments[1].overlap)

        # Edit u1 timing to 10->12 (no longer overlaps with u2 at 13->16)
        dirty_rows = [{
            "id": 1, "row_index": 1, "start": "10.000", "end": "12.000",
            "speaker": "SPK_01", "zh": "你好", "vi_subtitle": "Xin chào", "vi_dubbing": "Xin chào"
        }]

        self.controller.apply_manual_edits(dirty_rows)

        u1 = self.project.segments[0]
        self.assertEqual(u1.start, 10.0)
        self.assertEqual(u1.end, 12.0)
        self.assertEqual(u1.duration, 2.0)
        # Overlap recomputed: now False!
        self.assertFalse(u1.overlap)
        self.assertFalse(self.project.segments[1].overlap)
        # Other cues untouched (no drift)
        self.assertEqual(self.project.segments[1].start, 13.0)
        self.assertEqual(self.project.segments[2].start, 20.0)

    def test_apply_manual_edits_speaker(self):
        # Reassign u1 to SPK_02 (Bob)
        dirty_rows = [{
            "id": 1, "row_index": 1, "start": "10.000", "end": "14.000",
            "speaker": "SPK_02 · Bob", "zh": "你好", "vi_subtitle": "Xin chào", "vi_dubbing": "Xin chào"
        }]

        self.controller.apply_manual_edits(dirty_rows)

        u1 = self.project.segments[0]
        self.assertEqual(u1.speaker_id, "SPK_02")
        self.assertEqual(u1.speaker_name, "Bob")
        self.assertEqual(u1.tts_generation_status, "stale")

    def test_apply_multiple_dirty_rows(self):
        dirty_rows = [
            {"id": 1, "row_index": 1, "start": "10.000", "end": "14.000", "speaker": "SPK_01", "zh": "你好", "vi_subtitle": "Thoại 1", "vi_dubbing": "Thoại 1"},
            {"id": 3, "row_index": 3, "start": "20.000", "end": "22.000", "speaker": "SPK_01", "zh": "再见", "vi_subtitle": "Thoại 3", "vi_dubbing": "Thoại 3"},
        ]

        self.controller.apply_manual_edits(dirty_rows)

        self.assertEqual(self.project.segments[0].vi_subtitle, "Thoại 1")
        self.assertEqual(self.project.segments[2].vi_subtitle, "Thoại 3")

    def test_validation_failure_atomic(self):
        original_u1_sub = self.project.segments[0].vi_subtitle
        dirty_rows = [
            {"id": 1, "row_index": 1, "start": "10.000", "end": "14.000", "speaker": "SPK_01", "zh": "你好", "vi_subtitle": "Thoại hợp lệ", "vi_dubbing": "Thoại hợp lệ"},
            # Row 2 invalid: End <= Start!
            {"id": 2, "row_index": 2, "start": "15.000", "end": "12.000", "speaker": "SPK_02", "zh": "早安", "vi_subtitle": "Thoại lỗi", "vi_dubbing": "Thoại lỗi"},
        ]

        with self.assertRaises(ValueError) as ctx:
            self.controller.apply_manual_edits(dirty_rows)

        self.assertIn("Dòng 2 (ID 2): End (12.000) phải lớn hơn Start (15.000)", str(ctx.exception))
        # Verify row 1 was NOT mutated (atomic no-op)
        self.assertEqual(self.project.segments[0].vi_subtitle, original_u1_sub)


if __name__ == "__main__":
    unittest.main()
