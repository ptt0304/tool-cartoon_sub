from __future__ import annotations

import math
import os
import wave
import logging
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

from cartoon_sub.media.process import run_process
from cartoon_sub.project.cache import check_cancel
from cartoon_sub.speaker.models import Speaker
from cartoon_sub.subtitle.models import Project
from cartoon_sub.tts.cache_identity import (
    build_tts_fingerprint,
    build_tts_segment_id,
    normalize_tts_text,
    resolve_tts_text,
    TTS_SOURCE_DUBBING,
)
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
        false_overlaps = [(left, right) for left, right in zip(ordered, ordered[1:])
                          if right.start < left.end
                          and left.speaker_id == right.speaker_id
                          and left.speaker_id != "SPK_UNKNOWN"]
        if false_overlaps:
            left, right = false_overlaps[0]
            logging.getLogger(__name__).warning(
                "FALSE OVERLAP SAFETY: same speaker overlap detected: utt_%s -> utt_%s (%s)",
                left.id, right.id, left.speaker_id,
            )
            raise ValueError(
                f"FALSE_OVERLAP_SAFETY: Utterance {left.id} và {right.id} cùng speaker "
                "nhưng timestamp overlap. Hãy xác nhận/reconcile Speaker trước khi Build Dubbed Audio."
            )

        prepared: list[tuple[object, Path]] = []
        for utterance in project.utterances:
            check_cancel(cancel)
            phase_started = perf_counter()
            raw_speaker = project.speakers.get(utterance.speaker_id)
            speaker = Speaker(**raw_speaker) if raw_speaker else None
            current = False
            if cache_state == "valid" and speaker and speaker.tts_voice_id:
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
                    and entry.get("voice_id") == speaker.tts_voice_id
                    and entry.get("source", TTS_SOURCE_DUBBING) == text_source
                    and entry.get("text") == resolve_tts_text(utterance, text_source)
                    and float(entry.get("speed", -1)) == float(speaker.tts_speed)
                    and entry_path == stored_path
                )
            elif cache_state == "missing" and text_source == TTS_SOURCE_DUBBING:
                try:
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
        temporary = output_dir / "dubbed_mix.tmp.wav"
        filter_script = output_dir / "dubbed_mix.filter"
        temporary.unlink(missing_ok=True)

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
            os.replace(temporary, destination)
            from cartoon_sub.project.cache_status_service import write_dubbed_mix_state
            write_dubbed_mix_state(project, root, text_source)
            finalize_time = perf_counter() - finalize_started
            self.last_diagnostics = MixDiagnostics(
                segments_total=len(project.utterances), segments_ready=ready_count,
                metadata_and_identity=metadata_time, validate_wav=validate_time,
                duration_headers=duration_time, alignment=alignment_time,
                graph_build=graph_time, ffmpeg_timeline_mix=mix_time,
                finalize_replace=finalize_time, total=perf_counter() - total_started,
                ffprobe_processes=0, ffmpeg_processes=1,
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
            raise
        finally:
            filter_script.unlink(missing_ok=True)
