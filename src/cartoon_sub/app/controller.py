from pathlib import Path
import hashlib
import json
import os
import re
import wave
import logging
from cartoon_sub.project.project_manager import ProjectManager
from cartoon_sub.media.ffprobe import probe
from cartoon_sub.subtitle.parser import import_srt
from cartoon_sub.app.settings import LocalTTSSettings, SettingsStore
from cartoon_sub.project.cache import check_cancel
from cartoon_sub.project.stage_reset_service import ProjectStageResetService
from cartoon_sub.tts.generation_service import LocalTTSGenerationService
from cartoon_sub.tts.local_tts_client import LocalTTSClient
from cartoon_sub.tts.mix_service import TTSTimelineMixService
from cartoon_sub.transcription.pipeline import TranscriptionPipeline, save_subtitle_artifacts
from cartoon_sub.transcription.segmentation_normalizer import TranscriptSegmentationNormalizer
from cartoon_sub.transcription.mode import TranscriptSourceMode, normalize_mode, normalize_roi
from cartoon_sub.transcription.video.pipeline import VideoTranscriptionPipeline
from cartoon_sub.transcription.video.qaqc import VideoTranscriptQaqcService
from cartoon_sub.ai.openrouter_client import OpenRouterClient
from cartoon_sub.ai.model_resolver import AIModelResolver
from cartoon_sub.translation.context_service import (
    ContextService, enforce_context_language_contract, source_fingerprint,
)
from cartoon_sub.translation.pipeline import TranslationPipeline, mark_stale
from cartoon_sub.translation.source_modes import TranslationSourceService
from cartoon_sub.translation.qa_service import TranslationQAService
from cartoon_sub.translation.context_models import StoryContext
from cartoon_sub.translation.artifacts import save_translation_artifacts
from cartoon_sub.translation.glossary import parse_glossary
from cartoon_sub.translation.dubbing_service import DubbingService
from cartoon_sub.translation.master_dialogue_ai import MasterDialogueAIService
from cartoon_sub.subtitle.segmentation_service import SubtitleSegmentationService
from cartoon_sub.subtitle.export_service import export_current_srt
from cartoon_sub.tts.process_manager import LocalTTSProcessManager
from cartoon_sub.tts.duration_fit import VoiceCalibrationCache

class Controller:
    def __init__(self, settings_store=None):
        self.manager = ProjectManager()
        self.project = None
        self.directory = None
        self.settings_store = settings_store or SettingsStore()
        self.ai_model_resolver = AIModelResolver(self.settings_store)
        self.pipeline = TranscriptionPipeline(self.settings_store)
        self.video_pipeline = VideoTranscriptionPipeline(self.settings_store)
        self.video_qaqc_service = VideoTranscriptQaqcService()
        self.context_service = ContextService(self.settings_store)
        self.translation_pipeline = TranslationPipeline(self.settings_store)
        self.translation_source_service = TranslationSourceService()
        self.translation_qa_service = TranslationQAService(self.settings_store)
        self.dubbing_service=DubbingService(self.settings_store)
        self.master_dialogue_ai_service=MasterDialogueAIService(self.settings_store)
        self.segmentation_service=SubtitleSegmentationService()
        self.local_tts_manager = LocalTTSProcessManager()

    def validate_source_media(self, video, **job):
        return probe(video, require_audio=True, **job)

    def create(self, video, directory, metadata=None, **job):
        if metadata is None:
            metadata = self.validate_source_media(video, **job)
        project=self.manager.create(directory, video, metadata)
        project.dubbing_settings=self.settings_store.load_dubbing().to_dict()
        self.manager.save(project,directory)
        return project,Path(directory)

    def load(self, path):
        return self.manager.load(path), Path(path).parent

    def accept(self, result):
        self.project, self.directory = result
        force_candidate_upgrade = enforce_context_language_contract(self.project)
        restored_candidate = self.context_service.restore_cached_candidate(
            self.project, self.directory, force=force_candidate_upgrade)
        if force_candidate_upgrade or restored_candidate:
            self.manager.save(self.project, self.directory)
        mark_stale(self.project, self.settings_store.load())

    def save(self):
        if self.project:
            self.segmentation_service.sync_stale(self.project)
            mark_stale(self.project, self.settings_store.load())
            self.manager.save(self.project, self.directory)
            if self.project.transcription_status != "not_started":
                save_subtitle_artifacts(self.project, self.directory)
            if self.project.translation_status != "not_started" or (self.directory / "subtitle" / "vi.srt").exists():
                save_translation_artifacts(self.project, self.directory)

    def stage_reset_warnings(self, scope):
        if not self.project or not self.directory:
            return []
        return ProjectStageResetService(self.project, self.directory).protected_data(scope)

    def reset_stage(self, scope, *, clear_stt_cache=True):
        if not self.project or not self.directory:
            raise ValueError("Chưa mở project")
        from cartoon_sub.subtitle.models import Project
        before = Project.from_dict(self.project.to_dict())
        service = ProjectStageResetService(self.project, self.directory)
        try:
            if scope == "transcript":
                result = service.reset_transcript(clear_stt_cache=clear_stt_cache)
            elif scope == "context":
                result = service.reset_context()
            elif scope == "translation":
                result = service.reset_translation()
            elif scope == "subtitle":
                result = service.reset_subtitle()
            elif scope == "audio":
                result = service.reset_audio()
            else:
                raise ValueError(f"Reset scope không hợp lệ: {scope}")
            self.save()
            return result
        except Exception:
            self.project = before
            raise

    def clear_audio_cache(self, target):
        if not self.project or not self.directory:
            raise ValueError("Chưa mở project")
        result = ProjectStageResetService(self.project, self.directory).clear_audio_target(target)
        self.save()
        return result

    def clear_audio_transcript_cache(self):
        if not self.project or not self.directory:
            raise ValueError("Chưa mở project")
        result = ProjectStageResetService(self.project, self.directory).clear_audio_transcript_cache()
        self.save()
        return result

    def clear_video_transcript_cache(self):
        if not self.project or not self.directory:
            raise ValueError("Chưa mở project")
        result = ProjectStageResetService(self.project, self.directory).clear_video_transcript_cache()
        self.save()
        return result

    def invalidate_video_ocr_cache(self):
        if not self.project or not self.directory:
            raise ValueError("Chưa mở project")
        result = ProjectStageResetService(self.project, self.directory).invalidate_video_ocr_cache()
        self.save()
        return result

    def qa_video_transcript(self, **job):
        if not self.project or not self.directory:
            raise ValueError("Chưa mở project")
        result = self.video_qaqc_service.apply(self.project, self.directory)
        if result.applied:
            save_subtitle_artifacts(self.project, self.directory)
            self.manager.save(self.project, self.directory)
        return self.project, self.directory

    def undo_video_transcript_qaqc(self, **job):
        if not self.project or not self.directory:
            raise ValueError("Chưa mở project")
        self.project = self.video_qaqc_service.undo(self.project, self.directory)
        save_subtitle_artifacts(self.project, self.directory)
        self.manager.save(self.project, self.directory)
        return self.project, self.directory

    def sync_subtitle_presentation(self):
        """Persist only DisplaySegments stale against canonical Project.utterances."""
        changed = self.segmentation_service.sync_stale(self.project)
        if changed:
            self.save()
        return changed

    def import_subtitles(self, path):
        segments = TranscriptSegmentationNormalizer().normalize(
            import_srt(path), language="zh", source="srt")
        self.project.segments = segments
        for s in segments: s.translation_mode=self.project.dubbing_settings.get("mode","balanced_dubbing")
        self.project.transcription_status = "imported"
        self.project.translation_status = "not_started"
        self.project.translation_notes = {}
        self.project.translation_qa = {}
        self.project.segmentation_cache = {}
        self.project.context_proposal = {}
        self.project.context_proposal_hash = ""
        self.project.context_proposal_config_hash = ""
        self.project.context_status = "stale" if self.project.context_source_hash else "not_started"
        self.project.visual_context_status = "stale" if self.project.context_source_hash else "not_started"
        self.project.visual_context_signature = ""
        self.project.visual_context_error = ""
        self.project.final_audio_status = "stale"
        self.project.final_audio_fingerprint = ""
        self.project.cache_hashes.pop("translation", None)
        self.project.chunk_states.pop("translation", None)
        self.save()

    def import_vietnamese_subtitles(self, path):
        imported_rows = import_srt(path)
        if not imported_rows:
            raise ValueError("Vietnamese SRT không có subtitle hợp lệ")
        matched_ids = set()
        imported = unmatched = conflicts = 0
        for row in imported_rows:
            candidates = []
            for utterance in self.project.utterances:
                overlap = max(0.0, min(row.end, utterance.end) - max(row.start, utterance.start))
                shortest = min(row.end - row.start, utterance.end - utterance.start)
                near = abs(row.start - utterance.start) <= .75 and abs(row.end - utterance.end) <= .75
                if near or (shortest > 0 and overlap / shortest >= .5):
                    candidates.append((overlap / max(shortest, .001),
                                       -(abs(row.start - utterance.start) + abs(row.end - utterance.end)), utterance))
            candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
            if not candidates:
                unmatched += 1
                continue
            best = candidates[0][2]
            if best.id in matched_ids or (len(candidates) > 1 and candidates[0][:2] == candidates[1][:2]):
                conflicts += 1
                continue
            best.vi_subtitle = row.zh.strip()
            if not best.vi_dubbing.strip() or best.translation_source != "imported_srt":
                best.vi_dubbing = best.vi_subtitle
            best.translation_source = "imported_srt"
            self.project.translation_qa.pop(str(best.id), None)
            best.dubbing_optimized = False
            best.dubbing_status = "not_started"
            best.recalculate()
            self.segmentation_service.sync_utterance(self.project, best)
            matched_ids.add(best.id)
            imported += 1
        self.project.translation_status = "completed" if imported and imported == len(self.project.utterances) else "stale"
        self.project.cache_hashes.pop("translation", None)
        self.project.chunk_states.pop("translation", None)
        self.save()
        return {"imported": imported, "unmatched": unmatched, "conflicts": conflicts}

    def update_translation_options(self, preset, custom_prompt, glossary_text, genres,
                                   proper_name_mode="sino_vietnamese", custom_genre="",
                                   custom_style="", custom_name_rule=""):
        from cartoon_sub.translation.presets import GENRES, STYLES
        from cartoon_sub.translation.prompts import effective_custom_rules
        genres = list(dict.fromkeys(genres))
        if preset not in STYLES or any(g not in (*GENRES, "custom") for g in genres):
            raise ValueError("Thể loại/văn phong không hợp lệ")
        if len(genres) > 3:
            raise ValueError("Chỉ nên chọn tối đa 3 thể loại chính")
        if proper_name_mode in ("user_mapping", "Theo Mapping của user"):
            proper_name_mode = "custom"
        if proper_name_mode not in ("sino_vietnamese", "preserve_source", "custom"):
            raise ValueError("Chế độ tên riêng không hợp lệ")
        old_guidance = (
            self.project.translation_preset,
            self.project.translation_prompt,
            dict(self.project.glossary),
            list(self.project.translation_genres),
            self.project.proper_name_mode,
            effective_custom_rules(self.project),
        )
        glossary = parse_glossary(glossary_text)
        self.project.translation_preset = preset
        self.project.translation_prompt = custom_prompt
        self.project.glossary = glossary
        self.project.translation_genres = genres
        self.project.proper_name_mode = proper_name_mode
        self.project.translation_custom_genre = custom_genre
        self.project.translation_custom_style = custom_style
        self.project.translation_custom_name_rule = custom_name_rule
        new_guidance = (preset, custom_prompt, dict(glossary), list(genres), proper_name_mode,
                        effective_custom_rules(self.project))
        if old_guidance != new_guidance:
            if self.project.translation_status != "not_started":
                self.project.translation_status = "stale"
            self.project.translation_qa = {}
            self.project.translation_continuity_memory = []
        mark_stale(self.project, self.settings_store.load())

    def analyze_context(self, **job):
        settings = self.settings_store.load()
        capability = "vision_video" if settings.vision_input_mode == "video" else "vision_frames"
        resolved = self.ai_model_resolver.resolve("CONTEXT_ANALYSIS", "translate", (capability,))
        return self.context_service.analyze(self.project, self.directory,
            model=resolved.model_id, selection_source=resolved.source, **job)

    def apply_context(self, context):
        from cartoon_sub.translation.context_service import context_config_fingerprint
        valid_ids = {s.id for s in self.project.segments}
        approved = StoryContext.from_dict(context, valid_ids).to_dict()
        visual_ids = {row["id"] for row in approved.get("visual_contexts", [])}
        if visual_ids != valid_ids:
            raise ValueError("Không thể lưu: Visual Context chưa đầy đủ cho mọi ID transcript.")
        if self.project.visual_context_status not in {"proposal_ready", "applied"}:
            raise ValueError("Không thể lưu: Visual Context chưa được phân tích thành công từ video.")
        previous = self.project.story_context
        changed = previous != approved
        self.project.story_context = approved
        self.project.context_source_hash = source_fingerprint(self.project)
        self.project.context_approved_config_hash = context_config_fingerprint(self.project)
        self.project.context_status = "applied"
        self.project.visual_context_status = (
            "applied" if self.project.story_context.get("visual_contexts")
            else "VISUAL_CONTEXT_FAILED"
        )
        if self.project.story_context.get("visual_contexts"):
            from cartoon_sub.translation.visual_context import visual_source_signature
            try:
                self.project.visual_context_signature = visual_source_signature(self.project)
            except (OSError, ValueError):
                self.project.visual_context_status = "VISUAL_CONTEXT_FAILED"
                self.project.visual_context_signature = ""
        self.project.visual_context_error = ""
        if changed:
            if self.project.translation_status != "not_started":
                self.project.translation_status = "stale"
            self.project.translation_qa = {}
            for row in self.project.utterances:
                if row.dubbing_optimized or row.dubbing_status not in {"not_started", "stale"}:
                    row.dubbing_status = "stale"
                if row.tts_generation_status in {"generated", "cached"}:
                    row.tts_generation_status = "stale"
                    row.tts_error = ""
            self.project.final_audio_status = "stale"
            self.project.final_audio_fingerprint = ""
        self.save()

    def translate(self, **job):
        settings = self.settings_store.load()
        raw = (settings.tab_model_overrides or {}).get("translate")
        selection = settings.tab_selection("translate")
        from cartoon_sub.ai.runtime_debug import ai_debug
        ai_debug("Controller.translate", raw_model_value=raw, raw_type=type(raw).__name__,
                 normalized_selection=selection.to_dict(), provider=selection.provider,
                 model_id=selection.model_id, selection_type=type(selection).__name__,
                 calling_openrouter_resolver=selection.provider == "openrouter")
        model = self.ai_model_resolver.resolve("TRANSLATION", "translate", ("text",)).model_id
        source = getattr(self.project, "translate_source", None)
        if source is None:
            return self.translation_pipeline.run(self.project, self.directory, model=model, **job)
        working = self.translation_source_service.working_project(self.project, source)
        translated, _ = self.translation_pipeline.run(
            working, self.directory, model=model, persist=False, **job)
        self.translation_source_service.commit(self.project, source, translated)
        self.save()
        return self.project, self.directory

    def select_translate_source(self, source):
        if not self.project or not self.directory:
            raise ValueError("Chưa mở project")
        self.translation_source_service.select(self.project, source)
        self.save()
        return source

    def clear_translate_source_cache(self):
        if not self.project or not self.directory:
            raise ValueError("Chưa mở project")
        self.translation_source_service.clear_cache(self.project, self.directory, self.project.translate_source)
        self.save()

    @staticmethod
    def _cache_file_contains_selected_ids(path, selected_ids):
        """Return whether a durable AI cache record belongs to one of these rows."""
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return False
        pending = [payload]
        while pending:
            value = pending.pop()
            if isinstance(value, dict):
                if type(value.get("id")) is int and value["id"] in selected_ids:
                    return True
                ids = value.get("target_ids")
                if isinstance(ids, list) and any(type(item) is int and item in selected_ids for item in ids):
                    return True
                pending.extend(value.values())
            elif isinstance(value, list):
                pending.extend(value)
        return False

    def _invalidate_selected_retranslation_cache(self, selected_ids):
        """Remove only request/checkpoint records that contain one of the selected IDs."""
        for relative in ("cache/translation", "cache/dubbing"):
            folder = self.directory / relative
            if not folder.exists():
                continue
            for path in folder.rglob("*.json"):
                if self._cache_file_contains_selected_ids(path, selected_ids):
                    path.unlink(missing_ok=True)
        states = self.project.chunk_states.get("translation", {})
        for key, state in list(states.items()):
            if any(item in selected_ids for item in state.get("ids", [])):
                states.pop(key, None)

    def clear_selected_for_retranslation(self, ids):
        if not self.project:
            raise ValueError("Chưa mở project")
        selected_ids = set(ids)
        selected = [row for row in self.project.utterances if row.id in selected_ids]
        if not selected or {row.id for row in selected} != selected_ids:
            raise ValueError("Có dòng đã chọn không còn trong project")
        self._invalidate_selected_retranslation_cache(selected_ids)
        for segment in selected:
            segment.vi_subtitle = ""
            segment.vi_dubbing = ""
            segment.translation_source = "ai"
            segment.dubbing_optimized = False
            segment.dubbing_status = "not_started"
            segment.dubbing_fingerprint = ""
            segment.pre_optimization_vi_subtitle = None
            segment.pre_optimization_vi_dubbing = None
            segment.semantic_compression = False
            segment.meaning_preservation = "unknown"
            segment.allowed_audio_start = None
            segment.allowed_audio_end = None
            segment.tts_fit_ratio = None
            segment.dubbing_fit_status = "NOT_MEASURED"
            segment.dubbing_rewrite_attempts = 0
            segment.tts_generation_status = "stale"
            segment.tts_error = ""
            self.project.translation_notes.pop(str(segment.id), None)
            self.project.translation_notes.pop(f"dub:{segment.id}", None)
            self.project.translation_qa.pop(str(segment.id), None)
            self.segmentation_service.sync_utterance(self.project, segment)
            segment.recalculate()
        self.project.translation_status = "partial"
        self.project.final_audio_status = "stale"
        self.project.final_audio_fingerprint = ""
        self.save()
        return [segment.id for segment in selected]

    def retranslate_selected(self, ids, **job):
        model = self.ai_model_resolver.resolve("TRANSLATION", "translate", ("text",)).model_id
        return self.translation_pipeline.retranslate_selected(
            self.project, self.directory, ids, model=model, **job)

    def qa_translation(self, **job):
        selection = self.settings_store.load().tab_selection("translate")
        model = self.ai_model_resolver.resolve("TRANSLATION_QA", "translate", ("text",)).model_id
        return self.translation_qa_service.run(self.project, self.directory, model=model, **job)

    def qa_selected_translation(self, ids, **job):
        selection = self.settings_store.load().tab_selection("translate")
        model = self.ai_model_resolver.resolve("TRANSLATION_QA", "translate", ("text",)).model_id
        return self.translation_qa_service.run_selected_manual(
            self.project, self.directory, ids, model=model, **job)

    def master_dialogue_ai(self, ids, operation, **job):
        selection = self.settings_store.load().tab_selection("master_dialogue")
        return self.master_dialogue_ai_service.run(
            self.project, self.directory, ids, operation, selection, **job)

    def apply_master_dialogue_ai(self, operation, ids):
        changed = self.master_dialogue_ai_service.apply(self.project, self.directory, operation, ids)
        if changed:
            self.save()
        return changed

    def transcribe(self, **job):
        return self.pipeline.run(self.project, self.directory, **job)

    def transcribe_video(self, **job):
        return self.video_pipeline.run(self.project, self.directory, **job)

    def set_transcript_source_mode(self, mode):
        """Persist the Transcript acquisition mode (project field; no pipeline side effects)."""
        if not self.project:
            raise ValueError("Chưa mở project")
        mode = normalize_mode(mode)
        if self.project.transcript_source_mode != mode:
            self.project.transcript_source_mode = mode
            self.manager.save(self.project, self.directory)
        return mode

    def set_video_subtitle_roi(self, roi):
        """Persist the normalized Video-subtitle ROI through the project manager."""
        if not self.project:
            raise ValueError("Chưa mở project")
        roi = normalize_roi(roi)
        duration = float(self.project.metadata.get("duration", 0) or 0)
        regions = ([] if not roi or duration <= 0 else [{
            "id": "region_001", "start_time": 0.0, "end_time": duration, "roi": roi,
        }])
        if self.project.video_subtitle_roi != roi or self.project.video_subtitle_regions != regions:
            self.project.video_subtitle_roi = roi
            self.project.video_subtitle_regions = regions
            self.manager.save(self.project, self.directory)
        return roi

    def set_video_subtitle_regions(self, regions):
        if not self.project: raise ValueError("Chưa mở project")
        from cartoon_sub.transcription.video.regions import normalize_regions
        regions = normalize_regions(regions, self.project.metadata.get("duration", 0))
        self.project.video_subtitle_regions = regions
        self.project.video_subtitle_roi = dict(regions[0]["roi"]) if len(regions) == 1 else {}
        self.manager.save(self.project, self.directory)
        return regions

    def run_transcript(self, **job):
        """Route the Transcript action without changing the timeline on mode selection."""
        mode = normalize_mode(self.project.transcript_source_mode) if self.project else TranscriptSourceMode.AUDIO.value
        if mode == TranscriptSourceMode.VIDEO.value:
            return self.transcribe_video(**job)
        if mode == TranscriptSourceMode.IMPORT_SRT.value:
            raise ValueError("Chế độ Import Chinese SRT: hãy chọn file SRT để import.")
        return self.transcribe(**job)

    def test_connection(self, settings, entered_key="", **job):
        settings.validate()
        client = OpenRouterClient(self.settings_store.openrouter_key_pool())
        try:
            return client.test_connection(settings.transcription_model, **job)
        finally:
            client.close()

    def test_translation_connection(self, settings, entered_key="", **job):
        settings.validate()
        if settings.translation_provider == "openrouter":
            key = entered_key.strip() or self.settings_store.get_key("openrouter")
            client = OpenRouterClient(key)
        else:
            raise ValueError(
                "Cấu hình AI text cũ chưa được ánh xạ sang OpenRouter; "
                "hãy mở Settings > AI và chọn model từ catalog đã đồng bộ."
            )
        try:
            model = AIModelResolver(self.settings_store).resolve(
                "AI_CONNECTION_TEST", "translate", ("text",)).model_id
            return client.test_connection(model, **job)
        finally:
            client.close()

    def optimize_dubbing(self, ids, threshold=1, maximum_delta=0,
                         apply_threshold=True, **job):
        model = self.ai_model_resolver.resolve("DUBBING_OPTIMIZE", "translate", ("text",)).model_id
        return self.dubbing_service.optimize(
            self.project, self.directory, ids, threshold=threshold,
            maximum_delta=maximum_delta, apply_threshold=apply_threshold,
            model=model, **job)

    def revert_dubbing_optimization(self, ids):
        chosen = set(ids)
        if not chosen:
            raise ValueError("Chọn một hoặc nhiều câu đã Optimize for dubbing")
        reverted = []
        for segment in self.project.utterances:
            if segment.id not in chosen or segment.pre_optimization_vi_dubbing is None:
                continue
            subtitle_changed = segment.vi_subtitle != segment.pre_optimization_vi_subtitle
            dubbing_changed = segment.vi_dubbing != segment.pre_optimization_vi_dubbing
            segment.vi_subtitle = segment.pre_optimization_vi_subtitle
            segment.vi_dubbing = segment.pre_optimization_vi_dubbing
            segment.pre_optimization_vi_subtitle = None
            segment.pre_optimization_vi_dubbing = None
            segment.dubbing_optimized = False
            segment.dubbing_status = "not_started"
            segment.dubbing_fingerprint = ""
            segment.semantic_compression = False
            segment.meaning_preservation = "unknown"
            self.project.translation_notes.pop(f"dub:{segment.id}", None)
            if subtitle_changed:
                self.segmentation_service.sync_utterance(self.project, segment)
            if dubbing_changed and segment.tts_generation_status in {"generated", "cached"}:
                segment.tts_generation_status = "stale"
                segment.tts_error = ""
            segment.recalculate()
            reverted.append(segment.id)
        if not reverted:
            raise ValueError("Các câu đã chọn không có bản Optimize for dubbing để hoàn tác")
        self.project.final_audio_status = "stale"
        self.save()
        return reverted

    def export_transcript_srt(self):
        return export_current_srt(self.project, self.directory, "transcript")

    def export_translate_srt(self):
        return export_current_srt(self.project, self.directory, "translate")

    def export_subtitle_srt(self):
        self.sync_subtitle_presentation()
        return export_current_srt(self.project, self.directory, "subtitle")

    def _local_tts_client(self):
        return LocalTTSClient(self.settings_store.load_local_tts())

    def test_local_tts_connection(self, base_url, **job):
        current = self.settings_store.load_local_tts()
        current.base_url = base_url
        settings = current.validate()
        self.settings_store.save_local_tts(settings)
        check_cancel(job.get("cancel"))
        client = LocalTTSClient(settings)
        try:
            health = client.health()
            check_cancel(job.get("cancel"))
            library = client.voice_library()
            voices = [voice for voice in library["voices"] if voice.get("status") == "READY"]
            return {"health": health, "revision": library["revision"], "voices": voices,
                    "all_voice_ids": [voice.get("voice_id") for voice in library["voices"]]}
        finally:
            client.close()

    def ensure_local_tts_running(self, **job):
        settings = self.settings_store.load_local_tts()
        check_cancel(job.get("cancel"))
        ready, status = self.local_tts_manager.ensure_running(
            settings, cancel=job.get("cancel"), progress=job.get("progress")
        )
        check_cancel(job.get("cancel"))
        client = LocalTTSClient(settings)
        try:
            health = client.health()
            check_cancel(job.get("cancel"))
            library = client.voice_library()
            voices = [voice for voice in library["voices"] if voice.get("status") == "READY"]
            return {"health": health, "revision": library["revision"], "voices": voices,
                    "all_voice_ids": [voice.get("voice_id") for voice in library["voices"]],
                    "launch_status": status}
        finally:
            client.close()

    def fetch_local_tts_voice_library(self, timeout_seconds=2.0, **job):
        check_cancel(job.get("cancel"))
        client = LocalTTSClient(
            self.settings_store.load_local_tts(), request_timeout_seconds=timeout_seconds,
        )
        try:
            library = client.voice_library()
        finally:
            client.close()
        check_cancel(job.get("cancel"))
        return {
            "revision": library["revision"],
            "voices": [voice for voice in library["voices"] if voice.get("status") == "READY"],
            "all_voice_ids": [voice.get("voice_id") for voice in library["voices"]],
        }


    def set_local_tts_executable(self, path: str):
        settings = self.settings_store.load_local_tts()
        settings.local_tts_executable = path
        self.settings_store.save_local_tts(settings)

    def shutdown_local_tts(self):
        settings = self.settings_store.load_local_tts()
        if settings.stop_local_tts_on_exit:
            self.local_tts_manager.shutdown_owned_process()

    def load_local_tts_voices(self, **job):
        check_cancel(job.get("cancel"))
        client = self._local_tts_client()
        try:
            voices = client.list_ready_voices()
            check_cancel(job.get("cancel"))
            return voices
        finally:
            client.close()

    def update_project_tts_voice(self, voice_id):
        if not self.project:
            raise ValueError("Chưa mở project")
        current = getattr(self.project.audio_settings, "tts_voice_id", None)
        if current == (voice_id or None):
            return
        self.project.audio_settings.tts_voice_id = voice_id or None
        for utterance in self.project.utterances:
            utterance.dubbing_voice_id = self.project.audio_settings.tts_voice_id
            utterance.dubbing_estimated_rate = None
            utterance.dubbing_budget_duration = None
            utterance.allowed_audio_start = None
            utterance.allowed_audio_end = None
            utterance.tts_fit_ratio = None
            utterance.dubbing_fit_status = "NOT_MEASURED"
            utterance.dubbing_rewrite_attempts = 0
            if utterance.tts_generation_status in {"generated", "cached"}:
                utterance.tts_generation_status = "stale"
                utterance.tts_error = ""
        self.save()

    def preview_local_tts_voice(self, voice_id, **job):
        if not self.project or not self.directory:
            raise ValueError("Chưa mở project")
        check_cancel(job.get("cancel"))
        client = self._local_tts_client()
        try:
            wav_bytes = client.preview_voice(voice_id)
        finally:
            client.close()
        check_cancel(job.get("cancel"))
        cache_dir = self.directory / "cache" / "tts" / "previews"
        cache_dir.mkdir(parents=True, exist_ok=True)
        safe_voice_id = re.sub(r"[^A-Za-z0-9_-]", "_", voice_id).strip("_") or "voice"
        fingerprint = hashlib.sha256(voice_id.encode("utf-8")).hexdigest()[:12]
        destination = cache_dir / f"{safe_voice_id}_{fingerprint}.wav"
        temporary = destination.with_suffix(".tmp.wav")
        try:
            with temporary.open("wb") as handle:
                handle.write(wav_bytes)
                handle.flush()
                os.fsync(handle.fileno())
            with wave.open(str(temporary), "rb") as reader:
                if reader.getnframes() <= 0:
                    raise ValueError("Local_TTS preview WAV không hợp lệ")
            os.replace(temporary, destination)
            return destination
        except (OSError, EOFError, wave.Error) as exc:
            raise ValueError("Local_TTS preview WAV không hợp lệ") from exc
        finally:
            temporary.unlink(missing_ok=True)

    def generate_tts(self, **job):
        if not self.project or not self.directory:
            raise ValueError("Chưa mở project")
        settings = self.settings_store.load_local_tts()
        client = LocalTTSClient(settings)
        try:
            calibration_cache = VoiceCalibrationCache(
                Path(self.settings_store.folder) / "voice_duration_calibration.json"
            )
            service = LocalTTSGenerationService(
                client, self.manager, settings.base_url, calibration_cache,
                self.project.audio_settings.tts_text_source,
            )
            result = service.generate(self.project, self.directory, **job)
            self.manager.save(self.project, self.directory)
            return result
        finally:
            client.close()

    def update_tts_source(self, source):
        if not self.project:
            return
        if source not in {"vi_subtitle", "vi_dubbing"}:
            raise ValueError("Nguồn TTS không hợp lệ")
        if self.project.audio_settings.tts_text_source != source:
            self.project.audio_settings.tts_text_source = source
            for utterance in self.project.utterances:
                if utterance.tts_generation_status in {"generated", "cached"}:
                    utterance.tts_generation_status = "stale"
                    utterance.tts_error = ""
            self.project.final_audio_status = "stale"
            self.project.final_audio_fingerprint = ""
            self.save()

    def mix_tts(self, **job):
        if not self.project or not self.directory:
            raise ValueError("Chưa mở project")
        settings = self.settings_store.load_local_tts()
        result = TTSTimelineMixService().mix(
            self.project, self.directory, settings.base_url, **job
        )
        self.project.final_audio_status = "stale"
        self.save()
        return result

    def update_audio_settings(
        self,
        original_volume=None,
        dubbed_volume=None,
        additional_audio_path=...,
        additional_audio_volume=None,
        additional_audio_start=None,
    ):
        if not self.project:
            raise ValueError("Chưa mở project")
        current = self.project.audio_settings
        new_orig = current.original_volume if original_volume is None else int(round(original_volume))
        new_dub = current.dubbed_volume if dubbed_volume is None else int(round(dubbed_volume))
        new_path = current.additional_audio_path if additional_audio_path is Ellipsis else (str(additional_audio_path).strip() if additional_audio_path else None)
        new_add_vol = current.additional_audio_volume if additional_audio_volume is None else int(round(additional_audio_volume))
        new_start = current.additional_audio_start if additional_audio_start is None else float(additional_audio_start)

        if (
            new_orig != current.original_volume
            or new_dub != current.dubbed_volume
            or new_path != current.additional_audio_path
            or new_add_vol != current.additional_audio_volume
            or abs(new_start - current.additional_audio_start) > 1e-4
        ):
            from cartoon_sub.subtitle.models import AudioSettings
            self.project.audio_settings = AudioSettings(
                original_volume=new_orig,
                dubbed_volume=new_dub,
                additional_audio_path=new_path,
                additional_audio_volume=new_add_vol,
                additional_audio_start=new_start,
                tts_text_source=current.tts_text_source,
                tts_voice_id=current.tts_voice_id,
                tts_speed=current.tts_speed,
            ).validate()
            self.project.final_audio_status = "stale"
            self.save()

    def clear_additional_audio(self):
        if not self.project:
            raise ValueError("Chưa mở project")
        self.project.audio_settings.additional_audio_path = None
        self.project.audio_settings.additional_audio_start = 0.0
        self.project.final_audio_status = "stale"
        self.save()

    def mix_final_audio(self, **job):
        if not self.project or not self.directory:
            raise ValueError("Chưa mở project")
        from cartoon_sub.tts.final_mix_service import FinalAudioMixService
        result = FinalAudioMixService().mix(self.project, self.directory, **job)
        self.save()
        return result

    def render_export(self, test_mode=False, start=0.0, project_snapshot=None, **job):
        if not self.project or not self.directory:
            raise ValueError("Chưa mở project")
        if project_snapshot is None:
            self.sync_subtitle_presentation()
            project_snapshot = self.project
        from cartoon_sub.media.preview import VideoRenderer
        renderer = VideoRenderer()
        if test_mode:
            return renderer.render(
                project_snapshot,
                self.directory,
                start=start,
                preview=False,
                duration=30.0,
                use_final_audio=True,
                export_name="test_30s.mp4",
                **job,
            )
        else:
            return renderer.render(
                project_snapshot,
                self.directory,
                start=0.0,
                preview=False,
                duration=None,
                use_final_audio=True,
                export_name="final.mp4",
                **job,
            )

    def edit_utterance(self, sid, subtitle, dubbing, mode, target):
        from cartoon_sub.translation.modes import TranslationMode
        TranslationMode(mode)
        s=next(s for s in self.project.segments if s.id==sid)
        subtitle_changed = s.vi_subtitle != subtitle
        previous_dubbing = s.vi_dubbing
        s.vi_subtitle,s.vi_dubbing=subtitle,dubbing
        s.translation_mode=mode
        s.target_override=target or None
        s.dubbing_optimized=dubbing!=subtitle
        s.dubbing_status="manual"
        s.dubbing_fingerprint=""
        s.pre_optimization_vi_subtitle = None
        s.pre_optimization_vi_dubbing = None
        if previous_dubbing != dubbing:
            s.allowed_audio_start = None
            s.allowed_audio_end = None
            s.tts_fit_ratio = None
            s.dubbing_fit_status = "NOT_MEASURED"
            s.dubbing_rewrite_attempts = 0
        if previous_dubbing != dubbing and s.tts_generation_status in {"generated", "cached"}:
            s.tts_generation_status = "stale"
            s.tts_error = ""
        source_changed = (subtitle_changed if self.project.subtitle_text_source == "vi_subtitle"
                          else previous_dubbing != dubbing)
        if source_changed:
            self.segmentation_service.sync_utterance(self.project, s)
        if subtitle_changed or previous_dubbing != dubbing:
            from cartoon_sub.translation.qc import (
                local_dubbing_qa, local_translation_qa,
                store_dubbing_qa_result, store_qa_result,
            )
            if subtitle_changed:
                result = local_translation_qa(self.project, s)
                status = "MANUAL_FIXED" if result["status"] == "PASS" else (
                    "SUSPECT" if result["status"] == "SUSPECT" else "NEED_REVIEW")
                store_qa_result(self.project, s, status, result["issues"], 0,
                                ", ".join(item["type"] for item in result["issues"]))
            store_dubbing_qa_result(self.project, s, local_dubbing_qa(self.project, s))
        self.save()

    def apply_manual_edits(self, dirty_rows: list[dict]):
        if not self.project:
            raise ValueError("Chưa mở project")
        if not dirty_rows:
            return self.project

        # Step 1: Atomic pre-validation of all dirty rows
        validated = []
        for entry in dirty_rows:
            uid = entry["id"]
            row_idx = entry.get("row_index", uid)
            s = next((item for item in self.project.segments if item.id == uid), None)
            if s is None:
                raise ValueError(f"Dòng {row_idx} (ID {uid}): Không tìm thấy trong project")

            from cartoon_sub.subtitle.timestamps import parse_srt_timestamp
            try:
                start = parse_srt_timestamp(entry["start"])
            except (ValueError, TypeError):
                raise ValueError(f"Dòng {row_idx} (ID {uid}): Start '{entry['start']}' không phải là số hợp lệ")

            try:
                end = parse_srt_timestamp(entry["end"])
            except (ValueError, TypeError):
                raise ValueError(f"Dòng {row_idx} (ID {uid}): End '{entry['end']}' không phải là số hợp lệ")

            if start < 0:
                raise ValueError(f"Dòng {row_idx} (ID {uid}): Start ({start:.3f}) phải >= 0")
            if end <= start:
                raise ValueError(f"Dòng {row_idx} (ID {uid}): End ({end:.3f}) phải lớn hơn Start ({start:.3f})")

            validated.append({
                "segment": s,
                "uid": uid,
                "start": start,
                "end": end,
                "zh": entry.get("zh", ""),
                "vi_subtitle": entry.get("vi_subtitle", ""),
                "vi_dubbing": entry.get("vi_dubbing", ""),
            })

        # Step 2: Apply changes to canonical project
        tts_stale = False

        for v in validated:
            s = v["segment"]
            uid = v["uid"]
            start, end = v["start"], v["end"]
            new_zh = v["zh"]
            new_sub = v["vi_subtitle"]
            new_dub = v["vi_dubbing"]

            timing_changed = (abs(s.start - start) > 1e-4 or abs(s.end - end) > 1e-4)
            sub_changed = (s.vi_subtitle != new_sub)
            dub_changed = (s.vi_dubbing != new_dub)
            zh_changed = (s.zh != new_zh)

            if not (timing_changed or sub_changed or dub_changed or zh_changed):
                continue

            if timing_changed and s.display_segments:
                old_dur = s.duration if s.duration > 0 else 1.0
                new_dur = end - start
                for seg in s.display_segments:
                    r_s = max(0.0, (seg.start - s.start) / old_dur)
                    r_e = min(1.0, (seg.end - s.start) / old_dur)
                    seg.start = start + r_s * new_dur
                    seg.end = start + r_e * new_dur

            s.start = start
            s.end = end
            s.zh = new_zh
            s.vi_subtitle = new_sub
            s.vi_dubbing = new_dub
            s.dubbing_optimized = (new_dub != new_sub)
            s.dubbing_status = "manual"
            s.dubbing_fingerprint = ""
            s.pre_optimization_vi_subtitle = None
            s.pre_optimization_vi_dubbing = None

            source_changed = sub_changed if self.project.subtitle_text_source == "vi_subtitle" else dub_changed
            if source_changed or timing_changed:
                self.segmentation_service.sync_utterance(self.project, s)

            if dub_changed or timing_changed:
                s.allowed_audio_start = None
                s.allowed_audio_end = None
                s.tts_fit_ratio = None
                s.dubbing_fit_status = "NOT_MEASURED"
                s.dubbing_rewrite_attempts = 0
                if timing_changed:
                    s.dubbing_budget_duration = None
                if s.tts_generation_status in {"generated", "cached"}:
                    s.tts_generation_status = "stale"
                    s.tts_error = ""
                tts_stale = True

            if timing_changed:
                s.overlap_type = "NONE"
                s.overlap_diagnostics = []

            s.recalculate()
            if zh_changed:
                from cartoon_sub.translation.qc import (
                    invalidate_qa, local_dubbing_qa, store_dubbing_qa_result,
                )
                invalidate_qa(self.project, s)
                store_dubbing_qa_result(self.project, s, local_dubbing_qa(self.project, s))
            elif sub_changed or dub_changed:
                from cartoon_sub.translation.qc import (
                    local_dubbing_qa, local_translation_qa,
                    store_dubbing_qa_result, store_qa_result,
                )
                if sub_changed:
                    result = local_translation_qa(self.project, s)
                    status = "MANUAL_FIXED" if result["status"] == "PASS" else (
                        "SUSPECT" if result["status"] == "SUSPECT" else "NEED_REVIEW")
                    store_qa_result(self.project, s, status, result["issues"], 0,
                                    ", ".join(item["type"] for item in result["issues"]))
                store_dubbing_qa_result(self.project, s, local_dubbing_qa(self.project, s))

        if tts_stale:
            self.project.final_audio_status = "stale"

        self.save()
        return self.project

    def update_segmentation_settings(self, profile, settings):
        self.segmentation_service.update_settings(self.project, profile, settings)

    def update_subtitle_text_source(self, source):
        if source not in ("vi_subtitle", "vi_dubbing"):
            raise ValueError("Nguồn nội dung phụ đề không hợp lệ")
        if self.project.subtitle_text_source == source:
            return
        self.project.subtitle_text_source = source
        self.project.segmentation_cache = {}
        for utterance in self.project.utterances:
            utterance.set_display_segments([])
            self.segmentation_service.sync_utterance(self.project, utterance)
        self.save()

    def auto_segment(self, utterance_ids=None, **job):
        changed, skipped=self.segmentation_service.auto_segment(self.project,utterance_ids,
            cancel=job.get("cancel"), progress=job.get("progress"))
        self.save()
        if job.get("progress"):
            job["progress"](f"Đã segment {len(changed)} utterance; giữ {len(skipped)} bản chỉnh tay")
        return self.project,self.directory

    def reset_segmentation(self, utterance_ids):
        self.segmentation_service.reset(self.project,utterance_ids)
        self.save()

    def split_display_segment(self, utterance_id, display_id, word_index):
        self.segmentation_service.split_manual(self.project,utterance_id,display_id,word_index)
        self.save()

    def merge_display_segments(self, utterance_id, display_ids):
        self.segmentation_service.merge_manual(self.project,utterance_id,display_ids)
        self.save()

