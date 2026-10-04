"""Bounded audiovisual evidence for conversation translation."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from cartoon_sub.ai.openrouter_client import supports_capability
from cartoon_sub.media.process import run_process
from cartoon_sub.project.cache import content_hash


MEDIA_EVIDENCE_VERSION = "conversation-media-v1"
DEFAULT_PADDING_SECONDS = 2.0


@dataclass(frozen=True)
class ConversationEvidence:
    mode: str
    window: tuple[float, float]
    media: list[tuple[str, bytes]]
    fingerprint: str
    diagnostic: dict


def derive_media_window(targets, duration=None, padding=DEFAULT_PADDING_SECONDS):
    if not targets:
        raise ValueError("Conversation targets không được rỗng")
    start = max(0.0, min(float(row["start"]) for row in targets) - float(padding))
    end = max(float(row["end"]) for row in targets) + float(padding)
    if duration is not None and float(duration) > 0:
        end = min(float(duration), end)
    if end <= start:
        end = start + 0.25
    return round(start, 3), round(end, 3)


def evidence_mode(model_metadata):
    if model_metadata and supports_capability(model_metadata, "vision_video"):
        return "video_with_audio"
    if model_metadata and supports_capability(model_metadata, "vision_frames"):
        return "representative_frames"
    return "text_only"


class ConversationMediaBuilder:
    def __init__(self, ffmpeg_executable="ffmpeg"):
        self.ffmpeg_executable = ffmpeg_executable

    @staticmethod
    def _video_identity(source):
        stat = source.stat()
        return {"path": str(source.resolve()).casefold(), "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns}

    def _clip(self, source, destination, start, end):
        destination.parent.mkdir(parents=True, exist_ok=True)
        command = [
            self.ffmpeg_executable, "-nostdin", "-y", "-ss", f"{start:.3f}",
            "-i", str(source), "-t", f"{end - start:.3f}",
            "-map", "0:v:0", "-map", "0:a:0?", "-vf", "scale=-2:480",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "30",
            "-c:a", "aac", "-ac", "1", "-ar", "16000", "-b:a", "48k",
            "-movflags", "+faststart", str(destination),
        ]
        run_process(command, cwd=destination.parent)
        if not destination.is_file() or destination.stat().st_size <= 1024:
            raise ValueError("Không tạo được clip hội thoại có audio gốc")
        return command

    def _frame(self, source, destination, timestamp):
        destination.parent.mkdir(parents=True, exist_ok=True)
        command = [
            self.ffmpeg_executable, "-nostdin", "-y", "-ss", f"{timestamp:.3f}",
            "-i", str(source), "-frames:v", "1", "-vf", "scale=-2:480",
            "-q:v", "4", str(destination),
        ]
        run_process(command, cwd=destination.parent)
        if not destination.is_file() or destination.stat().st_size <= 512:
            raise ValueError("Không trích xuất được frame hội thoại")

    def build(self, project, directory, targets, model_metadata):
        duration = project.metadata.get("duration") if isinstance(project.metadata, dict) else None
        start, end = derive_media_window(targets, duration)
        mode = evidence_mode(model_metadata)
        source = Path(project.source_video_path)
        if not source.is_absolute():
            source = Path(directory) / source
        if mode == "text_only" or not source.is_file():
            fingerprint = content_hash({
                "version": MEDIA_EVIDENCE_VERSION, "mode": "text_only",
                "window": [start, end], "source_exists": source.is_file(),
            })
            return ConversationEvidence(
                "text_only", (start, end), [], fingerprint,
                {"mode": "text_only", "window": [start, end],
                 "original_audio": False, "reason": (
                     "model_no_visual_capability" if source.is_file() else "source_video_missing")},
            )

        identity = self._video_identity(source)
        fingerprint = content_hash({
            "version": MEDIA_EVIDENCE_VERSION, "mode": mode,
            "source": identity, "window": [start, end],
        })
        cache = Path(directory) / "cache" / "translation_media"
        if mode == "video_with_audio":
            path = cache / f"{fingerprint}.mp4"
            if not path.is_file() or path.stat().st_size <= 1024:
                self._clip(source, path, start, end)
            return ConversationEvidence(
                mode, (start, end), [("video/mp4", path.read_bytes())], fingerprint,
                {"mode": mode, "window": [start, end], "original_audio": True,
                 "media_count": 1},
            )

        span = max(0.01, end - start)
        times = sorted({round(start + span * ratio, 3) for ratio in (0.05, 0.35, 0.65, 0.95)})
        media = []
        for index, timestamp in enumerate(times):
            path = cache / "frames" / f"{fingerprint}-{index}.jpg"
            if not path.is_file() or path.stat().st_size <= 512:
                self._frame(source, path, timestamp)
            media.append(("image/jpeg", path.read_bytes()))
        return ConversationEvidence(
            mode, (start, end), media, fingerprint,
            {"mode": mode, "window": [start, end], "original_audio": False,
             "media_count": len(media), "frame_times": times},
        )
