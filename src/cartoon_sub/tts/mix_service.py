from __future__ import annotations

import math
import os
import wave
from pathlib import Path

from cartoon_sub.media.process import run_process
from cartoon_sub.project.cache import check_cancel
from cartoon_sub.speaker.models import Speaker
from cartoon_sub.subtitle.models import Project
from cartoon_sub.tts.cache_identity import build_tts_fingerprint, build_tts_segment_id


class TTSTimelineMixService:
    sample_rate = 48000
    channels = 2

    def __init__(self, ffmpeg_executable: str = "ffmpeg"):
        self.ffmpeg_executable = ffmpeg_executable

    @staticmethod
    def _valid_wav(path: Path) -> bool:
        try:
            if not path.is_file() or path.stat().st_size <= 44:
                return False
            with wave.open(str(path), "rb") as reader:
                return reader.getnchannels() > 0 and reader.getframerate() > 0 and reader.getnframes() > 0
        except (OSError, EOFError, wave.Error):
            return False

    def mix(
        self,
        project: Project,
        project_dir,
        server_base_url: str,
        cancel=None,
        progress=None,
    ) -> Path:
        root = Path(project_dir).resolve()
        duration = project.metadata.get("duration")
        if type(duration) not in (int, float) or not math.isfinite(duration) or duration <= 0:
            raise ValueError("Project metadata.duration phải là số dương")
        duration = float(duration)
        if not project.utterances:
            raise ValueError("Project không có Utterance để mix")

        inputs: list[tuple[Path, int]] = []
        for utterance in project.utterances:
            check_cancel(cancel)
            raw_speaker = project.speakers.get(utterance.speaker_id)
            try:
                speaker = Speaker(**raw_speaker) if raw_speaker else None
                current_fingerprint = (
                    build_tts_fingerprint(utterance, speaker, server_base_url)
                    if speaker and speaker.tts_voice_id
                    else ""
                )
                expected_segment_id = (
                    build_tts_segment_id(utterance, current_fingerprint)
                    if current_fingerprint
                    else ""
                )
            except (TypeError, ValueError):
                current_fingerprint = ""
                expected_segment_id = ""
            current = (
                utterance.tts_generation_status in {"generated", "cached"}
                and bool(utterance.tts_audio_path)
                and bool(utterance.tts_fingerprint)
                and bool(utterance.tts_segment_id)
                and utterance.tts_fingerprint == current_fingerprint
                and utterance.tts_segment_id == expected_segment_id
            )
            if not current:
                raise ValueError(
                    f"TTS_AUDIO_STALE: Utterance {utterance.id} must be regenerated before mixing."
                )
            path = (root / utterance.tts_audio_path).resolve()
            try:
                path.relative_to(root)
            except ValueError as exc:
                raise ValueError(f"Utterance {utterance.id} có đường dẫn TTS ngoài project") from exc
            if not self._valid_wav(path):
                raise ValueError(f"Utterance {utterance.id} có WAV TTS không hợp lệ")
            inputs.append((path, round(utterance.start * 1000)))

        output_dir = root / "audio" / "tts"
        output_dir.mkdir(parents=True, exist_ok=True)
        destination = output_dir / "dubbed_mix.wav"
        temporary = output_dir / "dubbed_mix.tmp.wav"
        temporary.unlink(missing_ok=True)

        filters = [
            f"[0:a]atrim=0:{duration:.6f},asetpts=PTS-STARTPTS[base]",
        ]
        labels = ["[base]"]
        for index, (_, delay_ms) in enumerate(inputs, 1):
            label = f"tts{index}"
            filters.append(
                f"[{index}:a]aresample={self.sample_rate},"
                f"aformat=sample_fmts=fltp:channel_layouts=stereo,"
                f"adelay={delay_ms}:all=1[{label}]"
            )
            labels.append(f"[{label}]")
        filters.append(
            f"{''.join(labels)}amix=inputs={len(labels)}:duration=longest:normalize=0,"
            f"alimiter=limit=0.95,atrim=0:{duration:.6f}[out]"
        )

        command = [
            self.ffmpeg_executable, "-nostdin", "-y",
            "-f", "lavfi", "-t", f"{duration:.6f}",
            "-i", f"anullsrc=r={self.sample_rate}:cl=stereo",
        ]
        for path, _ in inputs:
            command.extend(["-i", path])
        command.extend([
            "-filter_complex", ";".join(filters),
            "-map", "[out]", "-t", f"{duration:.6f}",
            "-ar", str(self.sample_rate), "-ac", str(self.channels),
            "-c:a", "pcm_s16le", temporary,
        ])

        try:
            check_cancel(cancel)
            run_process(command, cancel=cancel)
            check_cancel(cancel)
            if not self._valid_wav(temporary):
                raise RuntimeError("FFmpeg không tạo WAV mix hợp lệ")
            os.replace(temporary, destination)
            if progress:
                progress(f"TTS mix hoàn tất: {destination}")
            return destination
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
