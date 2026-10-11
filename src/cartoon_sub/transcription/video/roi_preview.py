"""One FFmpeg frame for ROI selection; no scanning or OCR."""
from pathlib import Path
from cartoon_sub.media.process import run_process
from cartoon_sub.project.paths import ProjectPaths


class VideoSubtitleRoiPreview:
    def frame_at(self, project, directory, moment, *, cancel=None, progress=None):
        duration = float(project.metadata.get("duration", 0) or 0)
        moment = min(duration, max(0.0, float(moment)))
        target = ProjectPaths(directory).transcript_video_cache_dir / "roi_preview.png"
        target.parent.mkdir(parents=True, exist_ok=True); target.unlink(missing_ok=True)
        run_process(["ffmpeg", "-nostdin", "-n", "-ss", str(moment), "-i", str(Path(project.source_video_path).resolve()), "-frames:v", "1", str(target)], cancel, progress)
        return target
    def frame(self, project, directory, *, cancel=None, progress=None):
        duration = float(project.metadata.get("duration", 0) or 0)
        moment = min(5.0, max(0.0, duration * .1))
        target = self.frame_at(project, directory, moment, cancel=cancel, progress=progress)
        if not target.is_file():
            raise ValueError("Không lấy được khung hình xem trước cho vùng phụ đề Video.")
        return target
