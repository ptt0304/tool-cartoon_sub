"""Local extraction, resumable transcription and subtitle artifact persistence."""
import json
import logging
import os
from dataclasses import asdict, replace
from pathlib import Path
from uuid import uuid4
from cartoon_sub.media.ffmpeg import FFmpeg
from cartoon_sub.media.ffprobe import probe
from cartoon_sub.media.process import CancelledError
from cartoon_sub.project.cache import atomic_json, file_hash, content_hash, check_cancel
from cartoon_sub.project.project_manager import ProjectManager
from cartoon_sub.project.paths import ProjectPaths
from cartoon_sub.subtitle.models import Project
from cartoon_sub.subtitle.parser import export_canonical_srt
from cartoon_sub.transcription.gemini_transcriber import GeminiTranscriber, PROMPT_VERSION
from cartoon_sub.transcription.segmentation_normalizer import TRANSCRIPT_SEGMENTATION_VERSION
from cartoon_sub.transcription.semantic_boundary_resolver import (
    ChineseSemanticBoundaryResolver,
    SEMANTIC_BOUNDARY_VERSION,
)
from cartoon_sub.ai.gemini_client import GeminiClient
from cartoon_sub.ai.openai_transcription_client import OpenAITranscriptionClient
from cartoon_sub.ai.openrouter_client import OpenRouterTranscriptionClient
from cartoon_sub.speaker.service import reconcile_overlaps, capture_initial_speaker_state
from cartoon_sub.speaker.resolution_service import SpeakerResolutionService

log = logging.getLogger(__name__)


TRANSCRIPTION_CLIENTS = {"gemini": GeminiClient, "openai": OpenAITranscriptionClient,
                         "openrouter": OpenRouterTranscriptionClient}


def resolve_transcript_stt_settings(settings):
    """Resolve the dedicated Transcript override before any media/API work."""
    override = str((settings.tab_model_overrides or {}).get("transcript_stt", "") or "").strip()
    model = override or str(settings.transcription_model or "").strip()
    if not model:
        raise ValueError(
            "Chưa chọn model STT.\n"
            "Hãy chọn model STT tại tab Transcript hoặc trong Settings → AI."
        )
    if override:
        return replace(settings, transcription_provider="openrouter", transcription_model=model)
    return settings


def transcript_timeline_signature(rows):
    return [(row.id, round(row.start, 6), round(row.end, 6), row.zh, row.speaker_id)
            for row in rows]


def invalidate_transcript_dependents(project):
    """Invalidate only derived state; source media and raw STT cache stay valid."""
    project.speakers = {}
    project.speaker_review_hash = ""
    project.speaker_review_initial_state = {}
    project.context_proposal = {}
    project.context_proposal_hash = ""
    project.context_proposal_config_hash = ""
    project.context_status = "stale" if project.context_source_hash else "not_started"
    project.visual_context_status = "stale" if project.context_source_hash else "not_started"
    project.visual_context_signature = ""
    project.visual_context_error = ""
    if project.translation_status != "not_started":
        project.translation_status = "stale"
    project.translation_notes = {}
    project.translation_qa = {}
    project.segmentation_cache = {}
    project.final_audio_status = "stale" if project.final_audio_status != "not_generated" else "not_generated"
    project.final_audio_fingerprint = ""
    project.cache_hashes.pop("translation", None)
    project.cache_hashes.pop("translation_run", None)
    project.chunk_states.pop("translation", None)


def resolve_transcription_client(settings, settings_store):
    from cartoon_sub.ai.text_client import model_metadata
    provider = settings.transcription_provider
    if provider == "openrouter":
        if not settings.transcription_model or "/" not in settings.transcription_model:
            raise ValueError("Hãy chọn model OpenRouter Speech-to-Text trong Settings > AI.")
    else:
        metadata = model_metadata(provider, settings.transcription_model)
        if not metadata or not metadata["capabilities"]["transcription"]:
            raise ValueError("Model đã chọn không có capability audio transcription.")
    client_factory = TRANSCRIPTION_CLIENTS.get(provider)
    if client_factory is None:
        raise ValueError("Provider/model này chưa được Cartoon_Sub hỗ trợ audio transcription.")
    if provider == "gemini":
        key_provider = lambda: settings_store.get_gemini_keys(settings)
    elif provider == "openrouter":
        key_provider = settings_store.openrouter_key_pool
    else:
        key_provider = lambda: (settings_store.get_api_key(provider, settings) if settings.api_key_file
                                else settings_store.get_key(provider))
    return provider, key_provider, client_factory


def save_subtitle_artifacts(project, directory):
    directory = Path(directory) / "subtitle"
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / f"zh-{uuid4().hex}.srt"
    try:
        export_canonical_srt(project.segments, temporary, "zh")
        os.replace(temporary, directory / "zh.srt")
    finally:
        temporary.unlink(missing_ok=True)
    atomic_json(directory / "segments.json", [asdict(s) for s in project.segments])
    live = Path(directory).parent / "exports" / "transcript"
    live.mkdir(parents=True, exist_ok=True)
    temporary = live / f"zh-{uuid4().hex}.srt"
    try:
        export_canonical_srt(project.segments, temporary, "zh")
        os.replace(temporary, live / "zh_transcript.srt")
    finally:
        temporary.unlink(missing_ok=True)


class TranscriptionPipeline:
    def __init__(self, settings_store, media=None, transcriber_factory=GeminiTranscriber):
        self.settings_store = settings_store
        self.media = media or FFmpeg()
        self.transcriber_factory = transcriber_factory

    def run(self, project, directory, *, cancel=None, progress=None):
        if project.transcription_status == "imported":
            raise ValueError("Project đã import SRT: không gọi AI transcription.")
        project = Project.from_dict(project.to_dict())
        if any(row.canonical_edit_source == "manual_split" for row in project.utterances):
            report = progress or (lambda text: None)
            report("Giữ timeline đã Tách dòng thủ công; không chạy lại STT/segmentation.")
            return project, Path(directory)
        # Re-probe here so projects created by older versions remain safe.
        # This happens before extraction and before constructing the transcriber.
        probe(project.source_video_path, cancel=cancel, progress=progress, require_audio=True)
        settings = resolve_transcript_stt_settings(self.settings_store.load())
        paths = ProjectPaths(directory).ensure()
        directory = paths.root
        provider, key_provider, client_factory = resolve_transcription_client(settings, self.settings_store)
        log.info("[OPENROUTER STT] %s", settings.transcription_model)
        log.info("[AI MODEL] feature=TRANSCRIPTION tab=transcript effective_model=%s "
                 "selection_source=SPECIALIZED_STT required_capabilities=transcription",
                 settings.transcription_model)
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
            raw_result = getattr(transcriber, "last_raw_result", None)
            semantic_stats = None
            if isinstance(raw_result, dict) and raw_result.get("segments"):
                resolved = ChineseSemanticBoundaryResolver(
                    self.settings_store,
                    directory / "cache" / "transcription_semantic",
                ).resolve(
                    raw_result["segments"], segments,
                    words=raw_result.get("words"), source="stt",
                    cancel=cancel, progress=report,
                )
                segments = resolved.utterances
                semantic_stats = resolved.stats
                check_cancel(cancel)
        except Exception as exc:
            project.transcription_status = "cancelled" if isinstance(exc, CancelledError) else "failed"
            manager.save(project, directory)
            raise
        same_timeline = transcript_timeline_signature(project.segments) == transcript_timeline_signature(segments)
        if same_timeline:
            # A repeated cached transcription must not erase a finished translation.
            for source, target in zip(project.segments, segments):
                target.vi_subtitle,target.vi_dubbing=source.vi_subtitle,source.vi_dubbing
                target.dubbing_optimized=source.dubbing_optimized
                target.translation_mode=source.translation_mode
                target.target_override=source.target_override
                target.semantic_compression=source.semantic_compression
                target.meaning_preservation=source.meaning_preservation
                target.dubbing_status="stale" if source.dubbing_optimized else source.dubbing_status
        else:
            invalidate_transcript_dependents(project)
        project.segments = segments
        if not same_timeline:
            for segment in segments: segment.translation_mode=project.dubbing_settings.get("mode","balanced_dubbing")
        speaker_stats = SpeakerResolutionService().resolve(project, provider)
        reconcile_overlaps(project.segments)
        capture_initial_speaker_state(project, replace=not same_timeline or not project.speaker_review_initial_state)
        project.transcription_status = "completed" if segments else "no_speech"
        project.selected_models["transcription"] = settings.transcription_model
        project.selected_models["transcription_provider"] = provider
        raw_fingerprint = content_hash({"source": source_hash, "provider": provider,
            "model": settings.transcription_model, "version": PROMPT_VERSION})
        project.cache_hashes["transcription_raw"] = raw_fingerprint
        project.cache_hashes["transcription"] = content_hash({
            "raw": raw_fingerprint, "segmentation_version": TRANSCRIPT_SEGMENTATION_VERSION})
        if semantic_stats and semantic_stats.ambiguous_regions:
            project.selected_models["semantic_boundary"] = semantic_stats.model
            project.cache_hashes["transcript_semantic"] = content_hash({
                "version": SEMANTIC_BOUNDARY_VERSION,
                "model": semantic_stats.model,
                "timeline": transcript_timeline_signature(segments),
            })
        manager.save(project, directory)
        save_subtitle_artifacts(project, directory)
        if project.translation_status != "not_started":
            from cartoon_sub.translation.artifacts import save_translation_artifacts
            save_translation_artifacts(project, directory)
        report(SpeakerResolutionService.message(speaker_stats))
        if semantic_stats and semantic_stats.ambiguous_regions:
            report(f"Semantic boundaries: {semantic_stats.ambiguous_regions} vùng; "
                   f"+{semantic_stats.added}/-{semantic_stats.removed}; "
                   f"request={semantic_stats.requests}, cache={semantic_stats.cache_hits}.")
        report(f"Hoàn tất {len(segments)} subtitle. Đã lưu subtitle/zh.srt và segments.json.")
        return project, directory
