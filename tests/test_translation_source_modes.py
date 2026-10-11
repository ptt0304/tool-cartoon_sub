import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication

from cartoon_sub.project.cache_status_service import CacheStatusService
from cartoon_sub.subtitle.models import Project, Utterance
from cartoon_sub.translation.source_modes import TranslationSourceService, timeline_hash
from cartoon_sub.ui.tabs import translate_tab


def video_project():
    return Project("p", "video.mp4", transcript_source_mode="video_subtitle", transcription_status="completed",
                   segments=[Utterance(1, 0, 1, "视频字幕", transcript_timing_provenance="VIDEO_SUBTITLE")])


class TranslationSourceModeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_selector_has_exact_three_sources(self):
        tab = translate_tab.build()
        try:
            self.assertEqual([tab.translate_source.itemData(index) for index in range(tab.translate_source.count())],
                             ["audio", "video", "srt"])
        finally:
            tab.close()

    def test_video_snapshot_is_owned_by_video_and_source_switch_does_not_change_timeline(self):
        project = video_project(); service = TranslationSourceService()
        before = [row.to_dict() for row in project.segments]
        service.select(project, "video")
        self.assertEqual(project.translate_source, "video")
        self.assertEqual([row.to_dict() for row in project.segments], before)
        self.assertEqual(project.translation_source_snapshots["video"]["timeline_hash"], timeline_hash(project.segments))
        with self.assertRaisesRegex(ValueError, "Audio"):
            service.select(project, "audio")
        self.assertEqual([row.to_dict() for row in project.segments], before)

    def test_srt_and_audio_ownership_are_not_inferred_from_video(self):
        project = video_project(); service = TranslationSourceService()
        self.assertTrue(service.available(project, "video"))
        self.assertFalse(service.available(project, "audio"))
        self.assertFalse(service.available(project, "srt"))
        project.transcript_source_mode = "audio"
        self.assertFalse(service.available(project, "audio"))

    def test_imported_srt_is_the_only_srt_source(self):
        project = Project("p", "video.mp4", transcript_source_mode="import_srt", transcription_status="imported",
                          segments=[Utterance(1, 0, 1, "导入字幕")])
        service = TranslationSourceService()
        service.select(project, "srt")
        self.assertEqual(project.translation_source_snapshots["srt"]["rows"][0]["zh"], "导入字幕")
        self.assertFalse(service.available(project, "video"))

    def test_cached_result_restores_without_api_and_preserves_timeline(self):
        project = video_project(); service = TranslationSourceService()
        service.select(project, "video")
        translated = service.working_project(project, "video")
        translated.segments[0].vi_subtitle = "Phụ đề video"
        translated.translation_status = "completed"
        service.commit(project, "video", translated)
        project.segments[0].vi_subtitle = ""
        before_zh = [(row.id, row.start, row.end, row.zh) for row in project.segments]
        service.select(project, "video")
        self.assertEqual(project.segments[0].vi_subtitle, "Phụ đề video")
        self.assertEqual([(row.id, row.start, row.end, row.zh) for row in project.segments], before_zh)

    def test_source_scoped_clear_preserves_other_translation_and_transcript_caches(self):
        project = video_project(); service = TranslationSourceService()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for source in ("audio", "video", "srt"):
                path = root / "cache" / "translation" / source
                path.mkdir(parents=True); (path / "checkpoint.json").write_text("{}", encoding="utf-8")
                project.translation_source_results[source] = {"translation_status": "completed"}
            transcript = root / "cache" / "transcript" / "video" / "ocr_results.json"
            transcript.parent.mkdir(parents=True); transcript.write_text("{}", encoding="utf-8")
            service.clear_cache(project, root, "audio")
            self.assertFalse((root / "cache" / "translation" / "audio").exists())
            self.assertTrue((root / "cache" / "translation" / "video").exists())
            self.assertTrue((root / "cache" / "translation" / "srt").exists())
            self.assertTrue(transcript.exists())
            status = CacheStatusService(project, root).translation()
            self.assertEqual(status["Translation Audio"], "STALE")
            self.assertEqual(status["Translation Video"], "CACHED")

