from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import time
import wave

from cartoon_sub.media.process import run_process
from cartoon_sub.project.cache import check_cancel
from cartoon_sub.subtitle.models import AudioSettings, Project


def compute_final_audio_fingerprint(
    duration: float,
    settings: AudioSettings,
    dubbed_mix_hash: str = "",
) -> str:
    data = {
        "duration": round(float(duration), 4),
        "original_volume": int(settings.original_volume),
        "dubbed_volume": int(settings.dubbed_volume),
        "additional_audio_path": settings.additional_audio_path or "",
        "additional_audio_volume": int(settings.additional_audio_volume),
        "additional_audio_start": round(float(settings.additional_audio_start), 4),
        "dubbed_mix_hash": dubbed_mix_hash,
    }
    encoded = json.dumps(data, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class FinalAudioMixService:
    sample_rate = 48000
    channels = 2

    def __init__(self, ffmpeg_executable: str = "ffmpeg", ffprobe_executable: str = "ffprobe"):
        self.ffmpeg_executable = ffmpeg_executable
        self.ffprobe_executable = ffprobe_executable

    @staticmethod
    def _valid_wav(path: Path) -> bool:
        try:
            if not path.is_file() or path.stat().st_size <= 44:
                return False
            with wave.open(str(path), "rb") as reader:
                return reader.getnchannels() > 0 and reader.getframerate() > 0 and reader.getnframes() > 0
        except (OSError, EOFError, wave.Error):
            return False

    def _validate_additional_audio(self, path: Path) -> None:
        if not path.is_file():
            raise ValueError(f"ADDITIONAL_AUDIO_NOT_FOUND: Không tìm thấy file âm thanh bổ sung '{path}'")
        try:
            cmd = [
                self.ffprobe_executable,
                "-v", "error",
                "-show_streams",
                "-select_streams", "a",
                "-of", "json",
                str(path),
            ]
            raw = json.loads(run_process(cmd))
            streams = raw.get("streams", [])
            if not streams:
                raise ValueError(f"ADDITIONAL_AUDIO_INVALID: File '{path}' không chứa luồng audio hợp lệ")
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError(f"ADDITIONAL_AUDIO_INVALID: File '{path}' không thể đọc được audio ({exc})") from exc

    def mix(
        self,
        project: Project,
        project_dir: str | Path,
        cancel=None,
        progress=None,
    ) -> Path:
        root = Path(project_dir).resolve()
        duration = project.metadata.get("duration")
        if type(duration) not in (int, float) or not math.isfinite(duration) or duration <= 0:
            raise ValueError("Project metadata.duration phải là số dương")
        duration = float(duration)

        settings = project.audio_settings if isinstance(project.audio_settings, AudioSettings) else AudioSettings(**(project.audio_settings or {}))
        settings.validate()

        # 1. Check original audio source
        source_audio = root / "audio" / "source.wav"
        if not source_audio.is_file():
            source_video = Path(project.source_video_path)
            if not source_video.is_absolute():
                source_video = root / source_video
            if source_video.is_file():
                source_audio = source_video
            else:
                source_audio = None

        # 2. Check dubbed_mix.wav
        dubbed_mix = root / "audio" / "tts" / "dubbed_mix.wav"
        if settings.dubbed_volume > 0 and not self._valid_wav(dubbed_mix):
            raise ValueError(
                "DUBBED_AUDIO_NOT_FOUND: Chưa tạo dubbed_mix.wav hoặc file bị lỗi. "
                "Vui lòng nhấn 'Build Dubbed Audio' trước khi mix Final Audio."
            )

        # 3. Check additional audio
        additional_path = None
        if settings.additional_audio_path:
            cand = Path(settings.additional_audio_path)
            if not cand.is_absolute():
                cand = root / cand
            self._validate_additional_audio(cand)
            additional_path = cand

        output_dir = root / "audio"
        output_dir.mkdir(parents=True, exist_ok=True)
        destination = output_dir / "final_audio.wav"
        temporary = output_dir / "final_audio.tmp.wav"
        temporary.unlink(missing_ok=True)

        inputs: list[str] = []
        filter_chains: list[str] = []
        active_labels: list[str] = []

        # Base silence generator
        inputs.extend([
            "-f", "lavfi",
            "-t", f"{duration:.6f}",
            "-i", f"anullsrc=r={self.sample_rate}:cl=stereo",
        ])
        filter_chains.append(f"[0:a]atrim=0:{duration:.6f},asetpts=PTS-STARTPTS[base]")
        active_labels.append("[base]")

        input_index = 1

        # Original Audio input
        if settings.original_volume > 0 and source_audio:
            inputs.extend(["-i", str(source_audio)])
            vol_factor = settings.original_volume / 100.0
            filter_chains.append(
                f"[{input_index}:a]aresample={self.sample_rate},"
                f"aformat=sample_fmts=fltp:channel_layouts=stereo,"
                f"volume={vol_factor:.4f}[orig]"
            )
            active_labels.append("[orig]")
            input_index += 1

        # Dubbed Audio input
        if settings.dubbed_volume > 0 and self._valid_wav(dubbed_mix):
            inputs.extend(["-i", str(dubbed_mix)])
            vol_factor = settings.dubbed_volume / 100.0
            filter_chains.append(
                f"[{input_index}:a]aresample={self.sample_rate},"
                f"aformat=sample_fmts=fltp:channel_layouts=stereo,"
                f"volume={vol_factor:.4f}[dub]"
            )
            active_labels.append("[dub]")
            input_index += 1

        # Additional Audio input
        if additional_path and settings.additional_audio_volume > 0:
            inputs.extend(["-i", str(additional_path)])
            vol_factor = settings.additional_audio_volume / 100.0
            delay_ms = round(settings.additional_audio_start * 1000)
            delay_filter = f",adelay={delay_ms}:all=1" if delay_ms > 0 else ""
            filter_chains.append(
                f"[{input_index}:a]aresample={self.sample_rate},"
                f"aformat=sample_fmts=fltp:channel_layouts=stereo,"
                f"volume={vol_factor:.4f}{delay_filter}[add]"
            )
            active_labels.append("[add]")
            input_index += 1

        filter_chains.append(
            f"{''.join(active_labels)}amix=inputs={len(active_labels)}:duration=longest:normalize=0,"
            f"alimiter=limit=0.95,atrim=0:{duration:.6f}[out]"
        )

        command = [
            self.ffmpeg_executable, "-nostdin", "-y",
            *inputs,
            "-filter_complex", ";".join(filter_chains),
            "-map", "[out]",
            "-t", f"{duration:.6f}",
            "-ar", str(self.sample_rate),
            "-ac", str(self.channels),
            "-c:a", "pcm_s16le",
            str(temporary),
        ]

        try:
            check_cancel(cancel)
            run_process(command, cancel=cancel)
            check_cancel(cancel)
            if not self._valid_wav(temporary):
                raise RuntimeError("FFmpeg không tạo WAV final hợp lệ")
            for attempt in range(5):
                try:
                    os.replace(temporary, destination)
                    break
                except PermissionError:
                    if attempt < 4:
                        time.sleep(0.06)
                    else:
                        raise ValueError(
                            f"FINAL_AUDIO_FILE_LOCKED: {destination.name} đang được một ứng dụng khác sử dụng "
                            "(ví dụ media player hoặc trình chỉnh sửa ngoài). Hãy đóng ứng dụng đang mở file và thử lại."
                        )
                except OSError as exc:
                    raise RuntimeError(f"FINAL_AUDIO_REPLACE_FAILED: Không thể thay thế {destination}: {exc}") from exc

            dubbed_hash = ""
            if dubbed_mix.is_file():
                dubbed_hash = str(dubbed_mix.stat().st_mtime_ns)
            project.final_audio_fingerprint = compute_final_audio_fingerprint(duration, settings, dubbed_hash)
            project.final_audio_status = "ready"

            if progress:
                progress(f"Mix Final Audio hoàn tất: {destination}")
            return destination
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
