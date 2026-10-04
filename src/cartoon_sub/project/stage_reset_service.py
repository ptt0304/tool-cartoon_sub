"""Dependency-aware reset operations for project processing stages."""
from dataclasses import dataclass, field
from pathlib import Path
import shutil

from cartoon_sub.translation.context_models import StoryContext


@dataclass
class StageResetResult:
    scope: str
    removed: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class ProjectStageResetService:
    """Reset canonical state first-class; filesystem cleanup is narrowly scoped."""

    def __init__(self, project, directory):
        self.project = project
        self.root = Path(directory).resolve()

    def protected_data(self, scope):
        warnings = []
        rows = self.project.utterances
        if scope == "transcript":
            count = sum(row.canonical_edit_source == "manual_split" for row in rows)
            if count:
                warnings.append(f"{count} dòng thuộc thao tác Tách dòng thủ công sẽ bị xóa")
            if self.project.speaker_review_hash:
                warnings.append("Speaker Review đã xác nhận sẽ bị xóa")
        if scope in {"transcript", "translation", "context"}:
            imported = sum(row.translation_source == "imported_srt" for row in rows)
            manual = sum(row.dubbing_status == "manual" for row in rows)
            if imported:
                warnings.append(f"{imported} bản dịch import thủ công sẽ bị xóa")
            if manual:
                warnings.append(f"{manual} dòng Subtitle/Dubbing sửa tay sẽ bị xóa")
        if scope == "subtitle":
            manual = sum(any(item.manual for item in row.display_segments) for row in rows)
            if manual:
                warnings.append(f"{manual} utterance có DisplaySegment chỉnh tay sẽ bị xóa")
        if scope == "audio" and any(row.dubbing_status == "manual" for row in rows):
            warnings.append("Nội dung Dubbing sửa tay sẽ được giữ; chỉ audio sinh ra bị xóa")
        return warnings

    def reset_transcript(self, clear_stt_cache=True):
        result = StageResetResult("transcript", warnings=self.protected_data("transcript"))
        project = self.project
        project.utterances = []
        project.transcription_status = "not_started"
        project.speakers = {}
        project.speaker_review_hash = ""
        project.speaker_review_initial_state = {}
        project.speaker_evidence = []
        project.speaker_proposals = {}
        self._clear_context_state(remove_approved=True)
        self._clear_translation_project_state()
        project.segmentation_cache = {}
        project.final_audio_status = "not_generated"
        project.final_audio_fingerprint = ""
        for key in ("transcription", "transcript_semantic", "translation", "translation_run",
                    "context_review_candidate_version"):
            project.cache_hashes.pop(key, None)
        if clear_stt_cache:
            project.cache_hashes.pop("transcription_raw", None)
        for key in ("translation", "dubbing"):
            project.chunk_states.pop(key, None)
        self._remove_files(result, (
            "subtitle/zh.srt", "subtitle/vi.srt", "subtitle/vi_dubbing.srt",
            "subtitle/segments.json", "subtitle/translation_review.json",
            "audio/final_audio.wav",
        ))
        self._remove_dirs(result, self._downstream_cache_dirs() + (
            "cache/audio_timing", "audio/tts", "cache/tts/previews",
        ))
        self._remove_context_response_cache(result)
        if clear_stt_cache:
            self._remove_dirs(result, ("cache/transcription", "cache/transcription_semantic"))
        return result

    def reset_context(self):
        result = StageResetResult("context", warnings=self.protected_data("context"))
        self._clear_context_state(remove_approved=True)
        self.project.speaker_evidence = []
        self.project.speaker_proposals = {}
        self._clear_translation_rows()
        self._clear_translation_project_state()
        self._clear_audio_rows()
        self._remove_context_response_cache(result)
        self._remove_dirs(result, self._downstream_cache_dirs() + (
            "cache/audio_timing", "audio/tts", "cache/tts/previews",
        ))
        self._remove_files(result, self._translation_artifacts() + ("audio/final_audio.wav",))
        return result

    def reset_translation(self):
        result = StageResetResult("translation", warnings=self.protected_data("translation"))
        self._clear_translation_rows()
        self._clear_translation_project_state()
        self._clear_audio_rows()
        self._remove_dirs(result, self._downstream_cache_dirs() + (
            "cache/audio_timing", "audio/tts", "cache/tts/previews",
        ))
        self._remove_files(result, self._translation_artifacts() + ("audio/final_audio.wav",))
        return result

    def reset_subtitle(self):
        result = StageResetResult("subtitle", warnings=self.protected_data("subtitle"))
        for row in self.project.utterances:
            row.set_display_segments([])
        self.project.segmentation_cache = {}
        self._remove_dirs(result, ("cache/audio_timing",))
        return result

    def reset_audio(self):
        result = StageResetResult("audio", warnings=self.protected_data("audio"))
        self._clear_audio_rows()
        self._remove_dirs(result, ("audio/tts", "cache/tts/previews"))
        self._remove_files(result, ("audio/final_audio.wav",))
        return result

    def clear_audio_target(self, target):
        if target == "audio_all":
            return self.reset_audio()
        if target == "dubbed_audio":
            result = StageResetResult(target)
            self._clear_dubbed_and_final(result)
            return result
        if target == "tts_all":
            result = StageResetResult(target)
            self._clear_tts_sources(result, {"vi_subtitle", "vi_dubbing"})
            self._clear_audio_rows()
            self._clear_dubbed_and_final(result)
            return result
        sources = {"tts_vi_subtitle": "vi_subtitle", "tts_vi_dubbing": "vi_dubbing"}
        if target not in sources:
            raise ValueError(f"Audio cache target không hợp lệ: {target}")
        source = sources[target]
        result = StageResetResult(target)
        self._clear_tts_sources(result, {source})
        if self.project.audio_settings.tts_text_source == source:
            self._clear_audio_rows()
            self._clear_dubbed_and_final(result)
        return result

    def _clear_tts_sources(self, result, sources):
        from cartoon_sub.tts.cache_manifest import (
            entry_utterance_cache_key, load_manifest, project_audio_path, save_manifest,
        )
        manifest, _ = load_manifest(self.root)
        segments = manifest.get("segments", {})
        removed_entries = []
        kept = {}
        for key, entry in segments.items():
            source = entry.get("source", "vi_dubbing")
            if source in sources:
                removed_entries.append((key, entry))
            else:
                kept[key] = entry
        manifest["segments"] = kept
        if removed_entries or (self.root / "audio" / "tts" / "tts_cache.json").exists():
            save_manifest(self.root, manifest)
        referenced = {entry.get("file") for entry in kept.values()}
        for key, entry in removed_entries:
            file_name = entry.get("file")
            if file_name and file_name not in referenced:
                try:
                    path = project_audio_path(self.root, file_name)
                    if path.is_file():
                        path.unlink(); result.removed.append(path.relative_to(self.root).as_posix())
                except (OSError, ValueError) as exc:
                    result.warnings.append(f"{file_name}: {exc}")
            result.removed.append(f"manifest:{entry_utterance_cache_key(key, entry)}:{entry.get('source', 'vi_dubbing')}")

    def _clear_dubbed_and_final(self, result):
        self._remove_files(result, (
            "audio/tts/dubbed_mix.wav", "audio/tts/dubbed_mix.json",
            "audio/tts/dubbed_mix.filter", "audio/final_audio.wav",
        ))
        self.project.final_audio_status = "not_generated"
        self.project.final_audio_fingerprint = ""

    def _clear_context_state(self, remove_approved):
        project = self.project
        project.context_proposal = {}
        project.context_proposal_hash = ""
        project.context_proposal_config_hash = ""
        project.visual_context_status = "not_started"
        project.visual_context_signature = ""
        project.visual_context_error = ""
        project.cache_hashes.pop("context_review_candidate_version", None)
        if remove_approved:
            project.story_context = StoryContext().to_dict()
            project.context_source_hash = ""
            project.context_approved_config_hash = ""
            project.context_status = "not_started"

    def _clear_translation_rows(self):
        for row in self.project.utterances:
            row.vi_subtitle = ""
            row.vi_dubbing = ""
            row.translation_source = "ai"
            row.dubbing_optimized = False
            row.dubbing_status = "not_started"
            row.dubbing_fingerprint = ""
            row.pre_optimization_vi_subtitle = None
            row.pre_optimization_vi_dubbing = None
            row.semantic_compression = False
            row.meaning_preservation = "unknown"
            row.set_display_segments([])
            row.recalculate()

    def _clear_translation_project_state(self):
        project = self.project
        project.translation_status = "not_started"
        project.translation_notes = {}
        project.translation_qa = {}
        project.translation_continuity_memory = []
        project.segmentation_cache = {}
        project.cache_hashes.pop("translation", None)
        project.cache_hashes.pop("translation_run", None)
        project.chunk_states.pop("translation", None)
        project.chunk_states.pop("translation_qa", None)
        project.chunk_states.pop("dubbing", None)

    def _clear_audio_rows(self):
        for row in self.project.utterances:
            row.tts_audio_path = None
            row.tts_duration = None
            row.tts_speed_factor = None
            row.tts_alignment_status = "not_imported"
            row.tts_alignment_diagnostic = ""
            row.allowed_audio_start = None
            row.allowed_audio_end = None
            row.tts_fit_ratio = None
            row.dubbing_fit_status = "NOT_MEASURED"
            row.dubbing_rewrite_attempts = 0
            row.dubbing_estimated_rate = None
            row.dubbing_budget_duration = None
            row.tts_segment_id = None
            row.tts_fingerprint = ""
            row.tts_generation_status = "not_generated"
            row.tts_error = ""
            row.recalculate()
        self.project.final_audio_status = "not_generated"
        self.project.final_audio_fingerprint = ""

    @staticmethod
    def _downstream_cache_dirs():
        return (
            "cache/translation", "cache/translation_qa", "cache/manual_translation_qa",
            "cache/translation_media", "cache/dubbing", "cache/dubbing_duration",
        )

    @staticmethod
    def _translation_artifacts():
        return (
            "subtitle/vi.srt", "subtitle/vi_dubbing.srt", "subtitle/segments.json",
            "subtitle/translation_review.json",
        )

    def _safe_path(self, relative):
        candidate = (self.root / relative).resolve()
        candidate.relative_to(self.root)
        return candidate

    def _remove_files(self, result, relatives):
        errors = []
        for relative in relatives:
            path = self._safe_path(relative)
            try:
                if path.is_file():
                    path.unlink()
                    result.removed.append(relative)
            except OSError as exc:
                errors.append(f"{relative}: {exc}")
        if errors:
            raise OSError("Không thể xóa đầy đủ artifact:\n" + "\n".join(errors))

    def _remove_dirs(self, result, relatives):
        errors = []
        for relative in relatives:
            path = self._safe_path(relative)
            try:
                if path.is_dir():
                    shutil.rmtree(path)
                    result.removed.append(relative + "/")
            except OSError as exc:
                errors.append(f"{relative}: {exc}")
        if errors:
            raise OSError("Không thể xóa đầy đủ cache:\n" + "\n".join(errors))

    def _remove_context_response_cache(self, result):
        errors = []
        cache = self._safe_path("cache/visual_context")
        if cache.is_dir():
            for path in cache.glob("*.json"):
                try:
                    path.unlink()
                    result.removed.append(path.relative_to(self.root).as_posix())
                except OSError as exc:
                    errors.append(f"{path.name}: {exc}")
        try:
            fusion = self._safe_path("cache/speaker_fusion")
            if fusion.is_dir():
                shutil.rmtree(fusion)
                result.removed.append("cache/speaker_fusion/")
        except OSError as exc:
            errors.append(f"cache/speaker_fusion: {exc}")
        if errors:
            raise OSError("Không thể xóa đầy đủ context cache:\n" + "\n".join(errors))
