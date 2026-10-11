import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cartoon_sub.project.project_manager import ProjectManager
from cartoon_sub.subtitle.models import Project, Utterance
from cartoon_sub.subtitle.parser import export_canonical_srt
from cartoon_sub.transcription.video.canonical_integration import (
    build_video_timeline, validate_video_timeline,
)
from cartoon_sub.transcription.video.pipeline import VideoTranscriptionPipeline


def event(event_id=1, start=1.0, end=2.0, text="你好", status="OCR_SUCCESS"):
    return {"event_id": event_id, "refined_start": start, "refined_end": end,
            "representative_time": (start + end) / 2, "ocr_status": status,
            "has_subtitle": status == "OCR_SUCCESS", "normalized_text": text}


class VideoCanonicalIntegrationTests(unittest.TestCase):
    def test_success_becomes_video_timed_canonical_row(self):
        result = build_video_timeline([event()], project_duration=10)
        row = result.utterances[0]
        self.assertEqual((row.start, row.end, row.zh), (1.0, 2.0, "你好"))
        self.assertEqual(row.transcript_timing_provenance, "VIDEO_SUBTITLE")

    def test_non_eligible_events_are_excluded(self):
        result = build_video_timeline([
            event(1), event(2, text="", status="OCR_EMPTY"), event(3, status="OCR_FAILED"),
            {**event(4), "normalized_text": ""},
        ], project_duration=10)
        self.assertEqual([row.id for row in result.utterances], [1])

    def test_no_duration_estimation_and_preserves_repeated_events(self):
        result = build_video_timeline([
            event(1, 1, 2, "同一句"), event(2, 2.2, 3, "同一句"),
            event(3, 3.1, 4, "不同句"),
        ], project_duration=10)
        self.assertEqual(result.same_text_merges, 0)
        self.assertEqual([(r.start, r.end, r.zh) for r in result.utterances],
                         [(1, 2, "同一句"), (2.2, 3, "同一句"), (3.1, 4, "不同句")])

    def test_rows_are_sorted_unique_and_invalid_duration_rejected(self):
        result = build_video_timeline([event(8, 3, 4, "乙"), event(2, 1, 2, "甲")], project_duration=10)
        self.assertEqual([(r.id, r.start) for r in result.utterances], [(1, 1), (2, 3)])
        with self.assertRaisesRegex(ValueError, "empty Chinese"):
            validate_video_timeline([Utterance(1, 1, 2, "")])
        with self.assertRaises(ValueError):
            build_video_timeline([event(1, 2, 1)], project_duration=10)

    def test_foreign_script_is_manual_review(self):
        result = build_video_timeline([event(1, text="안녕하세요")], project_duration=10)
        self.assertEqual(result.utterances[0].transcript_language_status, "NEEDS_MANUAL_REVIEW")
        self.assertEqual((result.language_mismatch, result.manual_review), (1, 1))

    def test_existing_exporter_preserves_video_timing(self):
        with tempfile.TemporaryDirectory() as folder:
            result = build_video_timeline([event(1, 1.234, 2.345)], project_duration=10)
            output = Path(folder) / "zh.srt"
            export_canonical_srt(result.utterances, output, "zh")
            body = output.read_text(encoding="utf-8")
            self.assertIn("00:00:01,234 --> 00:00:02,345", body)
            self.assertIn("你好", body)

    def test_candidate_failure_does_not_replace_existing_project_timeline(self):
        project = Project("p", "v.mp4", segments=[Utterance(1, 0, 1, "旧")])
        before = project.segments
        with self.assertRaises(ValueError):
            build_video_timeline([event(1, 2, 1)], project_duration=10)
        self.assertIs(project.segments, before)

    def test_video_pipeline_executes_pending_workload_and_commits_without_audio(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); source = root / "video.mp4"; source.write_bytes(b"v")
            project = Project("p", str(source), metadata={"width": 10, "height": 10, "duration": 10},
                              transcript_source_mode="video",
                              video_subtitle_roi={"x_pct": 0, "y_pct": 0, "w_pct": 1, "h_pct": 1})
            ProjectManager().save(project, root)
            from cartoon_sub.transcription.video.regions import regions_for_project, video_fingerprint
            from cartoon_sub.transcription.video.visual_detector import DETECTOR_VERSION
            manifest = {"video": str(source.resolve()), "roi": project.video_subtitle_roi,
                        "regions": regions_for_project(project), "video_fingerprint": video_fingerprint(source),
                        "detector_version": DETECTOR_VERSION, "candidate_count": 1,
                        "coarse_fps": 5, "refine_fps": 25,
                        "events": [{**event(), "region_id": "region_001", "status": "ACCEPTED"}]}
            target = root / "cache" / "transcript" / "video"; target.mkdir(parents=True)
            import json
            (target / "visual_events.json").write_text(json.dumps(manifest), encoding="utf-8")
            fake_ocr = type("FakeOcr", (), {"run": lambda *_args, **_kwargs: {
                "model": "google/gemini-2.5-flash", "model_source": "TAB_OVERRIDE",
                "metrics": {"ocr_total_events": 1, "ocr_cache_hits": 0, "ocr_api_events": 1,
                            "ocr_retries": 0, "ocr_success": 1, "ocr_empty": 0, "ocr_failed": 0},
                "events": [{**event(), "region_id": "region_001"}],
            }})()
            result, _ = VideoTranscriptionPipeline(object(), ocr_service=fake_ocr).run(project, root)
            self.assertEqual(result.transcription_status, "completed")
            self.assertEqual(result.selected_models["video_subtitle_ocr_workload"]["total_events"], 1)
            self.assertEqual(len(result.segments), 1)
            self.assertFalse((root / "audio" / "source.wav").exists())
            self.assertTrue((target / "ocr_workload.json").is_file())

    def test_manual_split_guard_keeps_existing_timeline(self):
        project = Project("p", "v.mp4", transcript_source_mode="video",
                          video_subtitle_roi={"x_pct": 0, "y_pct": 0, "w_pct": 1, "h_pct": 1},
                          segments=[Utterance(1, 0, 1, "Giữ nguyên", canonical_edit_source="manual_split")])
        result, _ = VideoTranscriptionPipeline(object()).run(project, Path("."))
        self.assertIs(result, project)
        self.assertEqual(result.segments[0].zh, "Giữ nguyên")


if __name__ == "__main__":
    unittest.main()
