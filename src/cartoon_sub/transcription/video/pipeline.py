"""Local video detection, resumable OCR, and canonical transcript integration."""
import json
from pathlib import Path

from cartoon_sub.media.process import CancelledError
from cartoon_sub.project.paths import ProjectPaths
from cartoon_sub.transcription.video.visual_detector import VideoSubtitleVisualDetector
from cartoon_sub.transcription.video.ocr_workload import append_workload_log, write_ocr_workload
from cartoon_sub.transcription.video.ocr_service import VideoSubtitleOcrService
from cartoon_sub.transcription.video.canonical_integration import build_video_timeline
from cartoon_sub.transcription.video.qaqc import VideoTranscriptQaqcService
from cartoon_sub.transcription.pipeline import invalidate_transcript_dependents, save_subtitle_artifacts, transcript_timeline_signature


class VideoTranscriptNotImplemented(ValueError):
    """Compatibility alias retained for callers from the deferred-workload phase."""


class VideoTranscriptionPipeline:
    def __init__(self, settings_store, *, detector=None, ocr_service=None):
        self.settings_store = settings_store
        self.detector = detector or VideoSubtitleVisualDetector()
        self.ocr_service = ocr_service or VideoSubtitleOcrService(settings_store)

    def run(self, project, directory, *, cancel=None, progress=None):
        if project.transcription_status == "imported":
            raise ValueError("Project đã import SRT: không chạy transcript từ Video.")
        report = progress or (lambda _text: None)
        if any(row.canonical_edit_source == "manual_split" for row in project.utterances):
            report("Giữ timeline đã Tách dòng thủ công; không ghi đè bằng transcript từ Video.")
            return project, Path(directory)
        from cartoon_sub.project.project_manager import ProjectManager
        from cartoon_sub.transcription.video.regions import regions_for_project, video_fingerprint
        from cartoon_sub.transcription.video.visual_detector import DETECTOR_VERSION

        regions = regions_for_project(project)
        if not regions:
            raise ValueError("Chưa chọn vùng phụ đề trên video cho chế độ Lấy transcript từ Video.")
        paths = ProjectPaths(directory)
        manager = ProjectManager()
        workload = VideoSubtitleOcrService.load_workload(directory)
        valid_workload = bool(workload and workload.get("video_fingerprint") == video_fingerprint(project.source_video_path)
                              and workload.get("detector_version") == DETECTOR_VERSION)
        if not valid_workload:
            manifest = None
            manifest_path = paths.transcript_video_cache_dir / "visual_events.json"
            try:
                candidate = json.loads(manifest_path.read_text(encoding="utf-8"))
                if (candidate.get("regions") == regions
                        and candidate.get("video") == str(Path(project.source_video_path).resolve())
                        and candidate.get("video_fingerprint") == video_fingerprint(project.source_video_path)
                        and candidate.get("detector_version") == DETECTOR_VERSION):
                    manifest = candidate
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                pass
            if manifest is None:
                project.transcription_status = "video_detecting"; manager.save(project, directory)
                manifest = self.detector.detect(project, directory, cancel=cancel, progress=report)
            workload = write_ocr_workload(paths.transcript_video_cache_dir / "ocr_workload.json", manifest.get("events", []), regions,
                video_fingerprint=manifest.get("video_fingerprint", ""), detector_version=manifest.get("detector_version", DETECTOR_VERSION))
            append_workload_log(workload, "cache/transcript/video/ocr_workload.json")
            project.selected_models["video_subtitle_detection"] = {"candidate_events": manifest["candidate_count"],
                "coarse_fps": manifest["coarse_fps"], "refine_fps": manifest["refine_fps"],
                "manifest": "cache/transcript/video/visual_events.json", "ai_eligible_events": workload["total_events"]}
            project.selected_models["video_subtitle_ocr_workload"] = {"manifest": "cache/transcript/video/ocr_workload.json",
                "total_events": workload["total_events"], "status": "PENDING"}
            project.transcription_status = "video_ocr_pending"; manager.save(project, directory)
            report("LOCAL_DETECTION_COMPLETE"); report(f"AI_ELIGIBLE_EVENTS={workload['total_events']}")
        project.transcription_status = "video_ocr_running"; manager.save(project, directory)
        try:
            ocr = self.ocr_service.run(project, directory, cancel=cancel, progress=report)
        except CancelledError:
            project.transcription_status = "video_ocr_cancelled"; manager.save(project, directory); raise
        project.transcription_status = "video_integrating"; manager.save(project, directory); report("Integrating Transcript...")
        result = build_video_timeline(ocr["events"], project_duration=project.metadata.get("duration"),
                                      translation_mode=project.dubbing_settings.get("mode", "balanced_dubbing"))
        same_timeline = transcript_timeline_signature(project.segments) == transcript_timeline_signature(result.utterances)
        if not same_timeline: invalidate_transcript_dependents(project)
        # A newly integrated generation must never be undoable through a
        # snapshot made for an older OCR/timeline generation.
        VideoTranscriptQaqcService().invalidate(project, directory)
        project.segments = result.utterances
        project.transcription_status = "completed" if result.utterances else "no_speech"
        project.selected_models["video_subtitle_ocr"] = {"model": ocr["model"], "source": ocr["model_source"], **ocr["metrics"]}
        project.selected_models.setdefault("video_subtitle_ocr_workload", {})["status"] = "COMPLETED"
        save_subtitle_artifacts(project, directory); manager.save(project, directory)
        report(f"Completed: {len(result.utterances)} subtitles")
        return project, Path(directory)
