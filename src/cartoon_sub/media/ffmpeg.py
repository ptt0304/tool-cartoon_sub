import logging
from pathlib import Path
from cartoon_sub.media.process import run_process

log = logging.getLogger(__name__)


class NoAudioStreamError(RuntimeError):
    """The selected media contains no audio stream to extract."""

    code = "NO_AUDIO_STREAM"

    def __init__(self, filename=None):
        name = f"\n\nTên file: {Path(filename).name}" if filename else ""
        super().__init__(
            "NO_AUDIO_STREAM\nVideo đã chọn không có luồng âm thanh nên không thể "
            "tạo transcript hoặc lồng tiếng từ audio.\n\nVui lòng kiểm tra lại "
            f"video đầu vào và chọn một video có âm thanh.{name}"
        )


class FFmpeg:
    def _run(self, video, output, options, cancel=None, progress=None):
        video = Path(video)
        output = Path(output)
        if video.resolve() == output.resolve():
            raise ValueError("Output must differ from source")
        if output.exists():
            raise FileExistsError(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        command = ["ffmpeg", "-nostdin", "-n", "-i", str(video), *options, str(output)]
        log.info("FFmpeg input=%r output=%r parent=%r exists=%r is_dir=%r",
                 str(video), str(output), str(output.parent), output.parent.exists(),
                 output.parent.is_dir())
        try:
            run_process(command, cancel, progress)
        except RuntimeError as exc:
            output.unlink(missing_ok=True)
            if "Output file does not contain any stream" in str(exc):
                raise NoAudioStreamError(video) from exc
            raise
        except Exception:
            output.unlink(missing_ok=True)
            raise
        return output

    def extract_audio(self, video, output, cancel=None, progress=None):
        return self._run(video, output, ["-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le"], cancel, progress)

    def extract_subtitle(self, video, stream_index, output, cancel=None, progress=None):
        # Text tracks only; FFmpeg reports an error for image subtitles (OCR is out of scope).
        return self._run(video, output, ["-map", f"0:{int(stream_index)}", "-c:s", "srt"], cancel, progress)
