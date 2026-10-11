import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from cartoon_sub.subtitle.models import Project
from cartoon_sub.transcription.video.ocr_service import (
    VideoSubtitleOcrService, language_status, normalize_ocr_text, validate_ocr_payload,
)


class FakeResolver:
    def __init__(self, model="google/test-vision"):
        self.model = model; self.calls = []
    def resolve(self, feature, tab, capabilities):
        self.calls.append((feature, tab, capabilities))
        return SimpleNamespace(model_id=self.model, source="TAB_OVERRIDE")


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses); self.calls = []; self.last_usage = {"cost": .01}
    def generate_multimodal_json(self, system, prompt, media, schema, model, **_kwargs):
        self.calls.append((system, prompt, media, schema, model))
        value = self.responses.pop(0)
        if isinstance(value, Exception): raise value
        return value
    def close(self): pass


class FakeStore:
    def __init__(self): self.client = None
    def load(self): return SimpleNamespace()
    def openrouter_catalog_cache(self): return {"models": []}
    def openrouter_key_pool(self): return "test-key"


def event(event_id=1, score=.1):
    return {"event_id": event_id, "refined_start": 1.0, "refined_end": 2.0,
            "representative_time": 1.5, "visual_score": score}


class VideoSubtitleOcrTests(unittest.TestCase):
    def project_and_manifest(self, folder, events=None):
        source = Path(folder) / "video.mp4"; source.write_bytes(b"video")
        project = Project("p", str(source), metadata={"width": 100, "height": 100, "duration": 10},
                          transcript_source_mode="video",
                          video_subtitle_roi={"x_pct": 0, "y_pct": 0, "w_pct": 1, "h_pct": 1})
        target = Path(folder) / "cache" / "transcript" / "video"; target.mkdir(parents=True)
        rows = [{**row, "region_id": row.get("region_id", "region_001"), "status": "ACCEPTED"}
                for row in (events or [event()])]
        (target / "visual_events.json").write_text(json.dumps({"video": str(source.resolve()), "roi": project.video_subtitle_roi,
            "events": rows}), encoding="utf-8")
        from cartoon_sub.transcription.video.ocr_workload import write_ocr_workload
        from cartoon_sub.transcription.video.regions import regions_for_project, video_fingerprint
        from cartoon_sub.transcription.video.visual_detector import DETECTOR_VERSION
        write_ocr_workload(target / "ocr_workload.json", rows, regions_for_project(project),
                           video_fingerprint=video_fingerprint(source), detector_version=DETECTOR_VERSION)
        return project

    def service(self, responses, model="google/test-vision"):
        self.store = FakeStore(); self.client = FakeClient(responses); self.resolver = FakeResolver(model)
        return VideoSubtitleOcrService(self.store, resolver=self.resolver,
            client_factory=lambda *_args: self.client,
            cropper=lambda *_args, **_kwargs: b"png")

    def test_model_routing_uses_transcript_video_key(self):
        with tempfile.TemporaryDirectory() as folder:
            payload = self.service([{"has_subtitle": True, "text": "你好"}]).run(self.project_and_manifest(folder), folder)
            self.assertEqual(self.resolver.calls[0], ("VIDEO_SUBTITLE_OCR", "transcript_video", ("vision_frames",)))
            self.assertEqual(payload["model"], "google/test-vision")

    def test_valid_true_false_and_normalization(self):
        self.assertEqual(normalize_ocr_text("  你  好\r\n世界  "), "你 好\n世界")
        self.assertEqual(validate_ocr_payload({"has_subtitle": True, "text": "你好"})["normalized_text"], "你好")
        self.assertEqual(validate_ocr_payload({"has_subtitle": False, "text": ""})["normalized_text"], "")

    def test_malformed_and_true_empty_are_rejected(self):
        for payload in ({}, {"has_subtitle": True, "text": ""}, {"has_subtitle": False, "text": "x"}):
            with self.assertRaises(ValueError): validate_ocr_payload(payload)

    def test_foreign_script_is_flagged(self):
        self.assertEqual(language_status("안녕하세요"), "SUSPICIOUS_FOREIGN_SCRIPT")
        self.assertEqual(language_status("你好 ABC"), "PASS")

    def test_retry_is_limited_to_one_and_keeps_timing(self):
        with tempfile.TemporaryDirectory() as folder:
            project = self.project_and_manifest(folder)
            original = dict(event())
            result = self.service([{"bad": True}, {"has_subtitle": True, "text": "你好"}]).run(project, folder)
            row = result["events"][0]
            self.assertEqual(len(self.client.calls), 2)
            self.assertEqual(row["retry_count"], 1)
            self.assertEqual((row["refined_start"], row["refined_end"]),
                             (original["refined_start"], original["refined_end"]))

    def test_cache_hit_avoids_second_api_call(self):
        with tempfile.TemporaryDirectory() as folder:
            project = self.project_and_manifest(folder)
            service = self.service([{"has_subtitle": True, "text": "你好"}])
            service.run(project, folder); again = service.run(project, folder)
            self.assertEqual(len(self.client.calls), 1)
            self.assertEqual(again["metrics"]["cache_hits"], 1)

    def test_changed_roi_or_model_invalidates_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            project = self.project_and_manifest(folder)
            service = self.service([{"has_subtitle": True, "text": "甲"}, {"has_subtitle": True, "text": "乙"}, {"has_subtitle": True, "text": "丙"}])
            service.run(project, folder)
            project.video_subtitle_roi = {"x_pct": .1, "y_pct": 0, "w_pct": .9, "h_pct": 1}
            service.run(project, folder)
            self.resolver.model = "google/other-vision"
            service.run(project, folder)
            self.assertEqual(len(self.client.calls), 3)

    def test_empty_and_result_id_remain_associated(self):
        with tempfile.TemporaryDirectory() as folder:
            project = self.project_and_manifest(folder, [event(7, score=0)])
            result = self.service([{"has_subtitle": False, "text": ""}]).run(project, folder)
            row = result["events"][0]
            self.assertEqual(row["event_id"], 7)
            self.assertEqual(row["ocr_status"], "OCR_EMPTY")
            self.assertEqual(len(self.client.calls), 1)

    def test_progress_starts_zero_and_terminal_events_count_once_with_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            project = self.project_and_manifest(folder, [event(1), {**event(2), "refined_start": 3.0, "refined_end": 4.0, "representative_time": 3.5}])
            progress = []
            self.service([{"bad": True}, {"has_subtitle": True, "text": "甲"}, {"has_subtitle": False, "text": ""}]).run(project, folder, progress=progress.append)
            self.assertEqual(progress[0], "Đang OCR Video: 0/2 event")
            self.assertEqual(progress[-1], "Đang OCR Video: 2/2 event")
            self.assertEqual(sum("1/2 event" in row for row in progress), 1)

    def test_cancel_preserves_completed_cache_and_rerun_resumes(self):
        from threading import Event
        from cartoon_sub.media.process import CancelledError
        with tempfile.TemporaryDirectory() as folder:
            project = self.project_and_manifest(folder, [event(1), {**event(2), "refined_start": 3.0, "refined_end": 4.0, "representative_time": 3.5}])
            cancel = Event()
            service = self.service([{"has_subtitle": True, "text": "甲"}, {"has_subtitle": True, "text": "乙"}])
            original = service._ocr_one
            def stop_after_first(*args, **kwargs):
                row = original(*args, **kwargs); cancel.set(); return row
            service._ocr_one = stop_after_first
            with self.assertRaises(CancelledError): service.run(project, folder, cancel=cancel)
            self.assertEqual(len(self.client.calls), 1)
            cancel.clear()
            service._ocr_one = original
            service.run(project, folder)
            self.assertEqual(len(self.client.calls), 2)


if __name__ == "__main__":
    unittest.main()
