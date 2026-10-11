import tempfile
import unittest
from pathlib import Path

from cartoon_sub.subtitle.models import Project, Utterance
from cartoon_sub.transcription.video.qaqc import (
    VideoTranscriptQaqcService, damerau_levenshtein, duplicate_reason,
    has_valid_video_timeline,
)


def row(identifier, start, end, text):
    return Utterance(identifier, start, end, text, transcript_timing_provenance="VIDEO_SUBTITLE")


def project(rows):
    return Project("video", "missing.mp4", transcript_source_mode="video_subtitle", metadata={"duration": 1000}, segments=rows,
                   video_subtitle_regions=[], transcription_status="completed")


class VideoTranscriptQaqcTests(unittest.TestCase):
    def apply(self, rows):
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        value = project(rows); service = VideoTranscriptQaqcService()
        return value, Path(folder.name), service, service.apply(value, folder.name)

    def test_required_75_78_cluster_uses_majority_and_full_lifespan(self):
        value, _folder, _service, report = self.apply([
            row(75, 620.540, 621.420, "这将是关乎人类族群生死存亡的大战"),
            row(76, 621.420, 622.240, "这将是关于人类族群生死亡存亡的大战。"),
            row(77, 622.240, 623.040, "这将是关乎人类族群生死存亡的大战"),
            row(78, 623.040, 623.260, "这将是关乎人类族群生死存亡的大战"),
        ])
        self.assertTrue(report.applied)
        self.assertEqual([(r.id, r.start, r.end, r.zh) for r in value.segments],
                         [(1, 620.540, 623.260, "这将是关乎人类族群生死存亡的大战")])
        self.assertEqual(report.rows_removed, 3)

    def test_exact_triple_six_and_punctuation_merge(self):
        rows = [row(i + 1, float(i), float(i + 1), "相同文本。" if i % 2 else "相同文本") for i in range(6)]
        value, _folder, _service, report = self.apply(rows)
        self.assertEqual(len(value.segments), 1)
        self.assertEqual(report.exact_duplicate_merges, 5)

    def test_near_ocr_noise_and_transposition(self):
        self.assertEqual(duplicate_reason("今天我们一起出发", "今天我们一同出发"), "NEAR")
        self.assertEqual(duplicate_reason("天地玄黄宇宙洪荒", "天地玄黄宇宙洪慌"), "NEAR")
        self.assertEqual(duplicate_reason("这是一段较长字幕用于验证两个字符差异", "这是一段较长字幕用于验证两个字错差异"), "NEAR")
        self.assertEqual(damerau_levenshtein("甲乙丙丁戊己", "甲乙丁丙戊己"), 1)

    def test_exact_pair_and_triple_clusters(self):
        value, _folder, _service, report = self.apply([
            row(1, 0, 1, "完全相同的长字幕"), row(2, 1, 2, "完全相同的长字幕"),
            row(3, 2, 3, "另一句独立字幕"), row(4, 3, 4, "三行重复的字幕"),
            row(5, 4, 5, "三行重复的字幕"), row(6, 5, 6, "三行重复的字幕"),
        ])
        self.assertTrue(report.applied)
        self.assertEqual([r.zh for r in value.segments], ["完全相同的长字幕", "另一句独立字幕", "三行重复的字幕"])

    def test_short_distant_and_middle_difference_do_not_merge(self):
        self.assertIsNone(duplicate_reason("你好啊", "你好吗"))
        value, _folder, _service, report = self.apply([row(1, 0, 1, "同一句"), row(2, 2, 3, "同一句")])
        self.assertFalse(report.applied); self.assertEqual(len(value.segments), 2)
        value, _folder, _service, report = self.apply([
            row(1, 0, 1, "长文本完全一致用于测试"), row(2, 1, 2, "这是不同的字幕内容"), row(3, 2, 3, "长文本完全一致用于测试")])
        self.assertFalse(report.applied); self.assertEqual(len(value.segments), 3)

    def test_reindexes_and_undo_restores_deep_original_without_overwriting_snapshot(self):
        rows = [row(1, 0, 1, "甲"), row(2, 1, 2, "重复文本"), row(3, 2, 3, "重复文本。"), row(4, 3, 4, "乙")]
        value, folder, service, report = self.apply(rows)
        before = project(rows).to_dict()
        self.assertTrue(report.applied); self.assertEqual([r.id for r in value.segments], [1, 2, 3])
        second = service.apply(value, folder)
        self.assertFalse(second.applied)
        restored = service.undo(value, folder)
        self.assertEqual(restored.to_dict()["master_timeline"], before["master_timeline"])
        self.assertFalse(service.undo_available(restored, folder))

    def test_stale_snapshot_is_rejected_and_invalidated(self):
        value, folder, service, report = self.apply([row(1, 0, 1, "重复文本"), row(2, 1, 2, "重复文本")])
        self.assertTrue(report.applied)
        value.segments[0].zh = "新的生成"
        self.assertFalse(service.undo_available(value, folder))
        with self.assertRaisesRegex(ValueError, "snapshot"):
            service.undo(value, folder)
        service.invalidate(value, folder)
        self.assertFalse(service.undo_available(value, folder))

    def test_cached_valid_video_timeline_is_available_without_current_session_status(self):
        value = project([row(1, 0, 1, "有效的视频字幕")])
        value.transcription_status = "video_ocr_pending"  # persisted rows, no current worker completion required
        with tempfile.TemporaryDirectory() as folder:
            service = VideoTranscriptQaqcService()
            self.assertTrue(has_valid_video_timeline(value))
            self.assertTrue(service.can_apply(value, folder))
            value.transcript_source_mode = "audio"
            self.assertFalse(service.can_apply(value, folder))
            value.transcript_source_mode = "import_srt"
            self.assertFalse(service.can_apply(value, folder))

    def test_audio_owned_or_empty_timeline_is_not_available(self):
        value = project([])
        with tempfile.TemporaryDirectory() as folder:
            service = VideoTranscriptQaqcService()
            self.assertFalse(service.can_apply(value, folder))
            value.segments = [Utterance(1, 0, 1, "音频来源")]
            self.assertFalse(has_valid_video_timeline(value))
            self.assertFalse(service.can_apply(value, folder))

    def test_applied_snapshot_disables_apply_and_enables_undo(self):
        value, folder, service, report = self.apply([row(1, 0, 1, "重复文本"), row(2, 1, 2, "重复文本")])
        self.assertTrue(report.applied)
        self.assertFalse(service.can_apply(value, folder))
        self.assertTrue(service.can_undo(value, folder))
        restored = service.undo(value, folder)
        self.assertTrue(service.can_apply(restored, folder))
        self.assertFalse(service.can_undo(restored, folder))

