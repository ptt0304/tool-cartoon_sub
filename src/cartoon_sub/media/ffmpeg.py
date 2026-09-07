from pathlib import Path
from cartoon_sub.media.process import run_process

class FFmpeg:
    def _run(self, video, output, options, cancel=None, progress=None):
        output = Path(output)
        if Path(video).resolve() == output.resolve():
            raise ValueError("Output must differ from source")
        if output.exists():
            raise FileExistsError(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        try:
            run_process(["ffmpeg", "-nostdin", "-n", "-i", video, *options, output], cancel, progress)
        except Exception:
            output.unlink(missing_ok=True)
            raise
        return output

    def extract_audio(self, video, output, cancel=None, progress=None):
        return self._run(video, output, ["-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le"], cancel, progress)

    def extract_subtitle(self, video, stream_index, output, cancel=None, progress=None):
        # Text tracks only; FFmpeg reports an error for image subtitles (OCR is out of scope).
        return self._run(video, output, ["-map", f"0:{int(stream_index)}", "-c:s", "srt"], cancel, progress)
