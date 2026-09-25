"""Local extraction, resumable transcription and subtitle artifact persistence."""
import json
import logging
import os
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4
from cartoon_sub.media.ffmpeg import FFmpeg
from cartoon_sub.media.ffprobe import probe
from cartoon_sub.media.process import CancelledError
from cartoon_sub.project.cache import atomic_json, file_hash, content_hash, check_cancel
from cartoon_sub.project.project_manager import ProjectManager
from cartoon_sub.project.paths import ProjectPaths
from cartoon_sub.subtitle.models import Project
from cartoon_sub.subtitle.parser import export_srt
from cartoon_sub.transcription.gemini_transcriber import GeminiTranscriber, PROMPT_VERSION
from cartoon_sub.ai.gemini_client import GeminiClient
from cartoon_sub.ai.openai_transcription_client import OpenAITranscriptionClient
from cartoon_sub.speaker.service import refresh_timeline, reconcile_overlaps, capture_initial_speaker_state

log = logging.getLogger(__name__)


TRANSCRIPTION_CLIENTS = {"gemini": GeminiClient, "openai": OpenAITranscriptionClient}


def resolve_transcription_client(settings, settings_store):
    from cartoon_sub.ai.text_client import model_metadata
    provider = settings.transcription_provider
    metadata = model_metadata(provider, settings.transcription_model)
    if not metadata or not metadata["capabilities"]["transcription"]:
        raise ValueError("Model đã chọn không có capability audio transcription.")
    client_factory = TRANSCRIPTION_CLIENTS.get(provider)
    if client_factory is None:
        raise ValueError("Provider/model này chưa được Cartoon_Sub hỗ trợ audio transcription.")
    if provider == "gemini":
        key_provider = lambda: settings_store.get_gemini_keys(settings)
    else:
        key_provider = lambda: (settings_store.get_api_key(provider, settings) if settings.api_key_file
                                else settings_store.get_key(provider))
    return provider, key_provider, client_factory


def save_subtitle_artifacts(project, directory):
    directory = Path(directory) / "subtitle"
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / f"zh-{uuid4().hex}.srt"
    try:
        export_srt(project.segments, temporary)
        os.replace(temporary, directory / "zh.srt")
    finally:
        temporary.unlink(missing_ok=True)
    atomic_json(directory / "segments.json", [asdict(s) for s in project.segments])


class TranscriptionPipeline:
    def __init__(self, settings_store, media=None, transcriber_factory=GeminiTranscriber):
        self.settings_store = settings_store
        self.media = media or FFmpeg()
        self.transcriber_factory = transcriber_factory

    def run(self, project, directory, *, cancel=None, progress=None):
        if project.transcription_status == "imported":
            raise ValueError("Project đã import SRT: không gọi AI transcription.")
        project = Project.from_dict(project.to_dict())
        # Re-probe here so projects created by older versions remain safe.
        # This happens before extraction and before constructing the transcriber.
        probe(project.source_video_path, cancel=cancel, progress=progress, require_audio=True)
        paths = ProjectPaths(directory).ensure()
        directory = paths.root
        settings = self.settings_store.load()
        provider, key_provider, client_factory = resolve_transcription_client(settings, self.settings_store)
        report = progress or (lambda text: None)
        report("Kiểm tra hash video và audio cache…")
        source_hash = file_hash(project.source_video_path, cancel)
        audio_path = paths.audio_dir / "source.wav"
        manifest_path = paths.audio_dir / "source.json"
        profile = "pcm_s16le-mono-16000-v1"
        reuse = False
        if audio_path.exists() and manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                reuse = (manifest.get("source_hash") == source_hash and manifest.get("profile") == profile
                         and manifest.get("audio_hash") == file_hash(audio_path, cancel))
            except (ValueError, AttributeError):
                pass
        if not reuse:
            report("FFmpeg: trích audio mono 16 kHz…")
            audio_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = paths.audio_dir / f"source-{uuid4().hex}.wav"
            log.info(
                "Source audio extraction project_root=%r audio_dir=%r output_path=%r "
                "output_parent=%r parent_exists=%r parent_is_dir=%r",
                str(paths.root), str(paths.audio_dir), str(temporary),
                str(temporary.parent), temporary.parent.exists(), temporary.parent.is_dir(),
            )
            try:
                self.media.extract_audio(project.source_video_path, temporary, cancel=cancel, progress=report)
                check_cancel(cancel)
                os.replace(temporary, audio_path)
            finally:
                temporary.unlink(missing_ok=True)
            atomic_json(manifest_path, {"source_hash": source_hash, "profile": profile,
                                        "audio_hash": file_hash(audio_path, cancel)})
        else:
            report("Dùng audio local đã trích xuất")
        check_cancel(cancel)
        project.transcription_status = "running"
        manager = ProjectManager()
        manager.save(project, directory)
        try:
            transcriber = self.transcriber_factory(key_provider, settings.transcription_model,
                                                   directory / "cache" / "transcription", settings.retry_count,
                                                   client_factory)
            segments = transcriber.transcribe(audio_path, cancel=cancel, progress=report)
            check_cancel(cancel)
        except Exception as exc:
            project.transcription_status = "cancelled" if isinstance(exc, CancelledError) else "failed"
            manager.save(project, directory)
            raise
        same_text = [(s.id, s.zh) for s in project.segments] == [(s.id, s.zh) for s in segments]
        if same_text:
            # A repeated cached transcription must not erase a finished translation.
            for source, target in zip(project.segments, segments):
                target.vi_subtitle,target.vi_dubbing=source.vi_subtitle,source.vi_dubbing
                target.dubbing_optimized=source.dubbing_optimized
                target.translation_mode=source.translation_mode
                target.target_override=source.target_override
                target.semantic_compression=source.semantic_compression
                target.meaning_preservation=source.meaning_preservation
                target.dubbing_status="stale" if source.dubbing_optimized else source.dubbing_status
        elif project.translation_status != "not_started":
            project.translation_status = "stale"
            project.translation_notes = {}
        project.segments = segments
        # A fresh diarization proposal must always be reviewed, even if the words match.
        project.speaker_review_hash=""
        if not same_text:
            project.speakers={}
            for segment in segments: segment.translation_mode=project.dubbing_settings.get("mode","balanced_dubbing")
        refresh_timeline(project)
        reconcile_overlaps(project.segments)
        capture_initial_speaker_state(project, replace=not same_text or not project.speaker_review_initial_state)
        project.transcription_status = "completed" if segments else "no_speech"
        project.selected_models["transcription"] = settings.transcription_model
        project.selected_models["transcription_provider"] = provider
        project.cache_hashes["transcription"] = content_hash({"source": source_hash,
            "provider": provider, "model": settings.transcription_model, "version": PROMPT_VERSION})
        manager.save(project, directory)
        save_subtitle_artifacts(project, directory)
        if project.translation_status != "not_started":
            from cartoon_sub.translation.artifacts import save_translation_artifacts
            save_translation_artifacts(project, directory)
        report(f"Hoàn tất {len(segments)} subtitle. Đã lưu subtitle/zh.srt và segments.json.")
        return project, directory
