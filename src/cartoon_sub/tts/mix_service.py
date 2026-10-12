from __future__ import annotations

import math
import os
import wave
import logging
import json
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

from cartoon_sub.media.process import run_process
from cartoon_sub.project.cache import check_cancel
from cartoon_sub.subtitle.models import Project
from cartoon_sub.tts.cache_identity import resolve_tts_text, TTS_SOURCE_DUBBING
from cartoon_sub.tts.cache_manifest import load_manifest, project_audio_path
from cartoon_sub.tts.cache_manifest import find_segment_entry
from cartoon_sub.tts.duration_fit import DurationFitPlanner


@dataclass(frozen=True)
class MixDiagnostics:
    segments_total: int
    segments_ready: int
    metadata_and_identity: float
    validate_wav: float
    duration_headers: float
    alignment: float
    graph_build: float
    ffmpeg_timeline_mix: float
    finalize_replace: float
    total: float
    ffprobe_processes: int
    ffmpeg_processes: int
    input_count: int
    filter_count: int
    filter_complex_chars: int
    cache: str = "NOT IMPLEMENTED"

    def format(self):
        return (
            "Build Dubbed Audio diagnostics\n"
            "-------------------------------\n"
            f"Segments total: {self.segments_total}\n"
            f"Segments ready: {self.segments_ready}\n"
            f"Metadata/identity:    {self.metadata_and_identity:.3f}s\n"
            f"Validate WAV:         {self.validate_wav:.3f}s\n"
            f"Duration WAV headers: {self.duration_headers:.3f}s\n"
            f"Alignment/auto-fit:   {self.alignment:.3f}s\n"
            f"FFmpeg graph build:   {self.graph_build:.3f}s\n"
            f"FFmpeg timeline mix:  {self.ffmpeg_timeline_mix:.3f}s\n"
            f"Finalize/replace:     {self.finalize_replace:.3f}s\n"
            f"FFprobe processes: {self.ffprobe_processes}\n"
            f"FFmpeg processes: {self.ffmpeg_processes}\n"
            f"Graph inputs: {self.input_count}\n"
            f"Graph filters: {self.filter_count}\n"
            f"Filter script chars: {self.filter_complex_chars}\n"
            f"Cache: {self.cache}\n"
            f"Total: {self.total:.3f}s"
        )


MASTER_TTS_FILTER = "volume=20dB,alimiter=limit=0.891251:level=false"


def _wav_metadata(path: Path, *, ffprobe_executable: str = "ffprobe", cancel=None) -> tuple[float, int, int]:
    try:
        with wave.open(str(path), "rb") as reader:
            rate = reader.getframerate()
            channels = reader.getnchannels()
            frames = reader.getnframes()
        if rate <= 0 or channels <= 0 or frames <= 0:
            raise RuntimeError(f"Final TTS WAV không hợp lệ: {path}")
        return frames / rate, rate, channels
    except wave.Error:
        raw = run_process([
            ffprobe_executable, "-v", "error", "-show_entries",
            "stream=sample_rate,channels,duration:format=duration", "-of", "json", str(path),
        ], cancel=cancel)
        payload = json.loads(raw)
        streams = payload.get("streams") or []
        if not streams:
            raise RuntimeError(f"Final TTS WAV không hợp lệ: {path}")
        stream = streams[0]
        duration = float(stream.get("duration") or (payload.get("format") or {}).get("duration") or 0)
        rate = int(stream.get("sample_rate") or 0)
        channels = int(stream.get("channels") or 0)
        if duration <= 0 or rate <= 0 or channels <= 0:
            raise RuntimeError(f"Final TTS WAV không hợp lệ: {path}")
        return duration, rate, channels


def process_final_tts_audio(input_path: Path, output_path: Path, *, ffmpeg_executable: str = "ffmpeg", ffprobe_executable: str = "ffprobe", cancel=None) -> Path:
    """Apply the one final master gain pass after timeline assembly, without touching segment WAVs."""
    input_path, output_path = Path(input_path), Path(output_path)
    temporary = output_path.with_name(f"{output_path.stem}.boosted.tmp.wav")
    temporary.unlink(missing_ok=True)
    duration_before, sample_rate, channels = _wav_metadata(
        input_path, ffprobe_executable=ffprobe_executable, cancel=cancel)
    logger = logging.getLogger(__name__)
    logger.info(
        "Final TTS master processing started | Input: %s | Gain: +20 dB | Limiter: 0.891251 | Codec: pcm_s24le",
        input_path,
    )
    command = [
        ffmpeg_executable, "-nostdin", "-y", "-i", str(input_path),
        "-af", MASTER_TTS_FILTER,
        "-c:a", "pcm_s24le", str(temporary),
    ]
    try:
        run_process(command, cancel=cancel)
        duration_after, output_rate, output_channels = _wav_metadata(
            temporary, ffprobe_executable=ffprobe_executable, cancel=cancel)
        tolerance = max(0.002, 1.0 / sample_rate)
        if output_rate != sample_rate or output_channels != channels:
            raise RuntimeError("Final TTS master processing changed sample rate or channel count")
        if abs(duration_after - duration_before) > tolerance:
            raise RuntimeError("Final TTS master processing changed duration")
        os.replace(temporary, output_path)
        logger.info("Final TTS master processing completed | Output: %s", output_path)
        return output_path
    except Exception:
        temporary.unlink(missing_ok=True)
        logger.exception("Final TTS master processing failed; original merged audio preserved")
        raise


class TTSTimelineMixService:
    sample_rate = 48000
    channels = 2

    def __init__(self, ffmpeg_executable: str = "ffmpeg"):
        self.ffmpeg_executable = ffmpeg_executable
        self.last_diagnostics = None

    @staticmethod
    def _valid_wav(path: Path) -> bool:
        try:
            if not path.is_file() or path.stat().st_size <= 44:
                return False
            with wave.open(str(path), "rb") as reader:
                return reader.getnchannels() > 0 and reader.getframerate() > 0 and reader.getnframes() > 0
        except (OSError, EOFError, wave.Error):
            return False

    @staticmethod
    def _wav_duration(path: Path) -> float:
        try:
            with wave.open(str(path), "rb") as reader:
                rate = reader.getframerate()
                frames = reader.getnframes()
                return frames / rate if rate > 0 else 0.0
        except (OSError, EOFError, wave.Error):
            return 0.0

    @staticmethod
    def _filter_file_name(path: Path, base: Path) -> str:
        """Return a filter-script-safe relative path (never put thousands of paths on argv)."""
        relative = os.path.relpath(path, base).replace("\\", "/")
        return relative.replace("'", r"\\'")

    def mix(
        self,
        project: Project,
        project_dir,
        server_base_url: str,
        cancel=None,
        progress=None,
    ) -> Path:
        total_started = perf_counter()
        metadata_time = validate_time = duration_time = alignment_time = 0.0
        ready_count = 0
        root = Path(project_dir).resolve()
        duration = project.metadata.get("duration")
        if type(duration) not in (int, float) or not math.isfinite(duration) or duration <= 0:
            raise ValueError("Project metadata.duration phải là số dương")
        duration = float(duration)
        if not project.utterances:
            raise ValueError("Project không có Utterance để mix")
        cache_manifest, cache_state = load_manifest(root)
        text_source = getattr(project.audio_settings, "tts_text_source", TTS_SOURCE_DUBBING)

        ordered = sorted(project.utterances, key=lambda row: (row.start, row.end, row.id))
        prepared: list[tuple[object, Path]] = []
        voice_id = getattr(project.audio_settings, "tts_voice_id", None)
        speed = float(getattr(project.audio_settings, "tts_speed", 1.0))
        for utterance in project.utterances:
            check_cancel(cancel)
            phase_started = perf_counter()
            current = False
            if cache_state == "valid" and voice_id:
                _, entry = find_segment_entry(
                    cache_manifest["segments"], utterance.tts_cache_key, text_source,
                )
                try:
                    entry_path = project_audio_path(root, entry.get("file", "")) if entry else None
                    stored_path = (root / utterance.tts_audio_path).resolve() if utterance.tts_audio_path else None
                except (TypeError, ValueError, OSError):
                    entry_path = stored_path = None
                current = bool(
                    entry
                    and utterance.tts_generation_status in {"generated", "cached"}
                    and utterance.tts_fingerprint == entry.get("signature")
                    and utterance.tts_segment_id == entry.get("segment_id")
                    and entry.get("voice_id") == voice_id
                    and entry.get("source", TTS_SOURCE_DUBBING) == text_source
                    and entry.get("text") == resolve_tts_text(utterance, text_source)
                    and float(entry.get("speed", -1)) == speed
                    and entry_path == stored_path
                )
            if not current:
                raise ValueError(
                    f"TTS_AUDIO_STALE: Utterance {utterance.id} chưa có TTS hợp lệ cho nguồn "
                    f"{text_source}. Hãy chạy Generate / Resume TTS trước."
                )
            path = (root / utterance.tts_audio_path).resolve()
            try:
                path.relative_to(root)
            except ValueError as exc:
                raise ValueError(f"Utterance {utterance.id} có đường dẫn TTS ngoài project") from exc
            metadata_time += perf_counter() - phase_started
            phase_started = perf_counter()
            if not self._valid_wav(path):
                raise ValueError(f"Utterance {utterance.id} có WAV TTS không hợp lệ")
            validate_time += perf_counter() - phase_started
            ready_count += 1

            actual_duration = utterance.tts_duration
            if not actual_duration:
                phase_started = perf_counter()
                actual_duration = self._wav_duration(path)
                duration_time += perf_counter() - phase_started
            if actual_duration <= 0:
                raise ValueError(f"Utterance {utterance.id} có WAV TTS không đo được duration")
            utterance.tts_duration = actual_duration
            prepared.append((utterance, path))

        phase_started = perf_counter()
        planner = DurationFitPlanner()
        planner.apply(project)
        inputs: list[tuple[Path, int, float]] = []
        for utterance, path in prepared:
            audio_start, tempo = planner.mix_parameters(utterance)
            inputs.append((path, round(audio_start * 1000), tempo))
        alignment_time += perf_counter() - phase_started

        graph_started = perf_counter()
        output_dir = root / "audio" / "tts"
        output_dir.mkdir(parents=True, exist_ok=True)
        destination = output_dir / "dubbed_mix.wav"
        raw_destination = output_dir / "dubbed_mix.raw.wav"
        temporary = output_dir / "dubbed_mix.raw.tmp.wav"
        boosted_temporary = output_dir / "dubbed_mix.boosted.tmp.wav"
        filter_script = output_dir / "dubbed_mix.filter"
        temporary.unlink(missing_ok=True)
        boosted_temporary.unlink(missing_ok=True)

        filters = [
            f"[0:a]atrim=0:{duration:.6f},asetpts=PTS-STARTPTS[base]",
        ]
        labels = ["[base]"]
        for index, (path, delay_ms, tempo) in enumerate(inputs, 1):
            label = f"tts{index}"
            tempo_filter = f"atempo={tempo:.4f}," if tempo > 1.001 else ""
            filters.append(
                f"amovie=filename='{self._filter_file_name(path, output_dir)}',{tempo_filter}aresample={self.sample_rate},"
                f"aformat=sample_fmts=fltp:channel_layouts=stereo,"
                f"adelay={delay_ms}:all=1[{label}]"
            )
            labels.append(f"[{label}]")
        filters.append(
            f"{''.join(labels)}amix=inputs={len(labels)}:duration=longest:normalize=0,"
            f"alimiter=limit=0.95,atrim=0:{duration:.6f}[out]"
        )

        # Windows CreateProcess has a finite command-line length.  A long video can have over a
        # thousand TTS WAVs, so both paths and the filter graph must live in a FFmpeg script file.
        filter_text = ";\n".join(filters)
        filter_script.write_text(filter_text, encoding="utf-8")
        command = [
            self.ffmpeg_executable, "-nostdin", "-y",
            "-f", "lavfi", "-t", f"{duration:.6f}",
            "-i", f"anullsrc=r={self.sample_rate}:cl=stereo",
        ]
        command.extend([
            "-filter_complex_script", filter_script,
            "-map", "[out]", "-t", f"{duration:.6f}",
            "-ar", str(self.sample_rate), "-ac", str(self.channels),
            "-c:a", "pcm_s16le", temporary,
        ])
        graph_time = perf_counter() - graph_started

        try:
            check_cancel(cancel)
            mix_started = perf_counter()
            run_process(command, cancel=cancel, cwd=output_dir)
            mix_time = perf_counter() - mix_started
            check_cancel(cancel)
            finalize_started = perf_counter()
            if not self._valid_wav(temporary):
                raise RuntimeError("FFmpeg không tạo WAV mix hợp lệ")
            os.replace(temporary, raw_destination)
            process_final_tts_audio(
                raw_destination, destination,
                ffmpeg_executable=self.ffmpeg_executable, cancel=cancel,
            )
            from cartoon_sub.project.cache_status_service import write_dubbed_mix_state
            write_dubbed_mix_state(project, root, text_source)
            finalize_time = perf_counter() - finalize_started
            self.last_diagnostics = MixDiagnostics(
                segments_total=len(project.utterances), segments_ready=ready_count,
                metadata_and_identity=metadata_time, validate_wav=validate_time,
                duration_headers=duration_time, alignment=alignment_time,
                graph_build=graph_time, ffmpeg_timeline_mix=mix_time,
                finalize_replace=finalize_time, total=perf_counter() - total_started,
                ffprobe_processes=0, ffmpeg_processes=2,
                input_count=len(inputs) + 1, filter_count=len(filters),
                filter_complex_chars=len(filter_text),
            )
            report = self.last_diagnostics.format()
            logging.getLogger(__name__).info("\n%s", report)
            if progress:
                progress(report)
            return destination
        except Exception:
            temporary.unlink(missing_ok=True)
            boosted_temporary.unlink(missing_ok=True)
            raise
        finally:
            filter_script.unlink(missing_ok=True)
