import logging
from pathlib import Path
from .chunker import translation_batches, source_rows
from .context_service import translation_readiness
from .conversation_media import ConversationMediaBuilder
from .prompts import EDITORIAL_RULES, editorial, translation_prompt, PROMPT_VERSION, effective_custom_rules
from cartoon_sub.speaker.service import refresh_timeline
from .gemini_translator import TRANSLATION_SCHEMA, validate_translation_response
from .requests import CachedRequests
from .checkpoint import TranslationCheckpointStore, chunk_fingerprint
from .artifacts import save_translation_artifacts
from .qa_service import TranslationQAService
from cartoon_sub.ai.gemini_client import GeminiClient
from cartoon_sub.ai.text_client import text_client_factory
from cartoon_sub.project.cache import check_cancel, content_hash
from cartoon_sub.project.project_manager import ProjectManager
from cartoon_sub.subtitle.models import Project
from cartoon_sub.media.process import CancelledError
from cartoon_sub.ai.provider_errors import AIProviderError, ProviderErrorCategory


log = logging.getLogger(__name__)


def translation_failure_message(exc, completed, total, last_completed_id=None):
    category = getattr(exc, "error_category", ProviderErrorCategory.UNKNOWN)
    saved = f"Đã lưu {completed}/{total} câu."
    resume = " Bạn có thể bấm Dịch để tiếp tục từ phần chưa hoàn thành."
    messages = {
        ProviderErrorCategory.RATE_LIMITED:
            "OpenRouter đang giới hạn tốc độ yêu cầu.",
        ProviderErrorCategory.INSUFFICIENT_CREDITS:
            "OpenRouter báo không đủ credit để tiếp tục.",
        ProviderErrorCategory.PAYMENT_REQUIRED:
            "OpenRouter yêu cầu thanh toán để tiếp tục.",
        ProviderErrorCategory.KEY_BUDGET_EXCEEDED:
            "OpenRouter báo key đã đạt giới hạn ngân sách.",
        ProviderErrorCategory.TIMEOUT:
            "Yêu cầu dịch bị timeout.",
        ProviderErrorCategory.NETWORK_ERROR:
            "Không thể kết nối OpenRouter.",
        ProviderErrorCategory.SERVER_ERROR:
            "OpenRouter tạm thời gặp lỗi máy chủ.",
        ProviderErrorCategory.BAD_REQUEST:
            "Yêu cầu dịch không hợp lệ.",
        ProviderErrorCategory.MODEL_ERROR:
            "Model hiện tại không thể xử lý yêu cầu này.",
        ProviderErrorCategory.AUTH_INVALID:
            "OpenRouter không còn API key hợp lệ để tiếp tục.",
        ProviderErrorCategory.RESPONSE_ERROR:
            "OpenRouter trả kết quả không đúng định dạng yêu cầu.",
    }
    prefix = messages.get(category, "Không thể tiếp tục bản dịch do lỗi OpenRouter.")
    if last_completed_id is not None:
        saved += f" ID hoàn thành cuối: {last_completed_id}."
    return f"{prefix}\n{saved}{resume}"


class TranslationResumableError(RuntimeError):
    def __init__(self, message, cause):
        super().__init__(message)
        self.cause = cause
        self.error_category = getattr(cause, "error_category", ProviderErrorCategory.UNKNOWN)
        self.status_code = getattr(cause, "status_code", None)


def translation_fingerprint(project, settings, model=None):
    model = model or (settings.tab_model_overrides or {}).get("translate") or settings.default_ai_model
    return content_hash({"source": [{"id": row.id, "zh": row.zh,
                                     "start": row.start, "end": row.end}
                                    for row in project.segments],
                        "guidance": {"genres": project.translation_genres,
                                     "style": project.translation_preset,
                                     "proper_name_mode": project.proper_name_mode,
                                     "proper_name_rules": project.glossary,
                                     "user_defined_context": project.translation_prompt.strip()},
                        "active_custom_rules": effective_custom_rules(project),
                        "system": EDITORIAL_RULES, "provider": settings.translation_provider, "model": model,
                        "chunk_size": settings.translation_chunk_size, "version": PROMPT_VERSION,
                        "imported_vi": {s.id: s.vi_subtitle for s in project.segments
                                        if s.translation_source == "imported_srt"}})


def mark_stale(project, settings):
    previous = project.cache_hashes.get("translation")
    if previous and previous != translation_fingerprint(project, settings):
        project.translation_status = "stale"


class TranslationPipeline:
    CONTINUITY_MEMORY_LIMIT = 64

    def __init__(self, store, client_factory=GeminiClient, media_builder=None):
        self.store, self.factory = store, client_factory
        self.qa_service = TranslationQAService(store, client_factory)
        self.media_builder = media_builder or ConversationMediaBuilder()

    @classmethod
    def _update_continuity_memory(cls, project, updates):
        by_key = {str(item.get("key", "")).strip().casefold(): dict(item)
                  for item in project.translation_continuity_memory
                  if isinstance(item, dict) and str(item.get("key", "")).strip()}
        for item in updates or []:
            if not isinstance(item, dict):
                continue
            key, value = str(item.get("key", "")).strip(), str(item.get("value", "")).strip()
            confidence = item.get("confidence", 0)
            if (not key or not value or len(key) > 120 or len(value) > 240
                    or type(confidence) not in (int, float) or float(confidence) < .70):
                continue
            by_key[key.casefold()] = {"key": key, "value": value,
                                      "confidence": min(1.0, float(confidence))}
        project.translation_continuity_memory = list(by_key.values())[-cls.CONTINUITY_MEMORY_LIMIT:]

    def run(self, project, directory, *, model=None, cancel=None, progress=None):
        refresh_timeline(project)
        ready, reason = translation_readiness(project)
        if not ready:
            raise ValueError(reason)
        project = Project.from_dict(project.to_dict())
        canonical_before = [(row.id, row.start, row.end, row.zh) for row in project.segments]
        settings = self.store.load()
        imported_ids = {s.id for s in project.segments if s.translation_source == "imported_srt"}
        # Keep the original full-timeline lookaround. Imported rows remain useful
        # context, but are removed from request targets so AI cannot overwrite them.
        all_batches = []
        for targets, before, after in translation_batches(project.segments, settings.translation_chunk_size):
            targets = [row for row in targets if row["id"] not in imported_ids]
            if targets:
                all_batches.append((targets, before, after))
        if not all_batches:
            project.translation_status = "completed"
            ProjectManager().save(project, directory)
            save_translation_artifacts(project, directory)
            return project, Path(directory)
        if model is None:
            from cartoon_sub.ai.model_resolver import AIModelResolver
            model = AIModelResolver(self.store).resolve(
                "TRANSLATION", "translate", ("text",)).model_id
        catalog = self.store.openrouter_catalog_cache() or {"models": []}
        model_metadata = next((item for item in catalog.get("models", [])
                               if item.get("id") == model), None)
        manager = ProjectManager()
        fingerprint = translation_fingerprint(project, settings, model)
        states = project.chunk_states.setdefault("translation", {})
        project.cache_hashes["translation_run"] = fingerprint
        # Rebuild continuity deterministically by replaying durable checkpoints in order.
        # This avoids using a later persisted state as the input of chunk 1 after restart.
        project.translation_continuity_memory = []
        project.translation_status = "in_progress"
        project.selected_models["translation"] = model
        manager.save(project, directory)
        cache_directory = Path(directory) / "cache" / "translation"
        checkpoints = TranslationCheckpointStore(cache_directory)
        factory = (self.factory if self.factory is not GeminiClient
                   else text_client_factory(settings.translation_provider))
        requests = CachedRequests(self.store, cache_directory, model,
            settings.retry_count, cancel, progress, factory, settings.translation_provider)
        translated = []
        by_id = {s.id: s for s in project.segments}
        def apply_row(row):
            segment = by_id[row['id']]
            segment.vi = row['vi']
            if not segment.dubbing_optimized:
                segment.meaning_preservation = row['meaning_preservation']
                segment.semantic_compression = row['compressed']
            project.translation_notes[str(row['id'])] = row['review_note']
        current = None
        current_ids = []
        completed_ids = set(imported_ids)
        total_rows = len(project.segments)
        try:
            for index, (targets, before, after) in enumerate(all_batches, 1):
                current = str(index)
                ids = [r["id"] for r in targets]
                current_ids = ids
                evidence = self.media_builder.build(
                    project, directory, targets, model_metadata)
                project.selected_models["translation_evidence_mode"] = evidence.mode
                continuity_before = [dict(item) for item in project.translation_continuity_memory]
                chunk_key = chunk_fingerprint(
                    project, settings, model, targets, before, after,
                    continuity_before, evidence.fingerprint,
                )
                short_key = chunk_key[:12]
                cached = checkpoints.load(chunk_key, ids)
                log.info(
                    "[TRANSLATE] chunk=%s/%s target_ids=%s fingerprint=%s cache=%s model=%s",
                    index, len(all_batches), f"{ids[0]}-{ids[-1]}", short_key,
                    "HIT" if cached else "MISS", model,
                )
                if cached:
                    rows = cached["translations"]
                    for row in rows:
                        apply_row(row)
                    translated.extend(rows)
                    project.translation_continuity_memory = [
                        dict(item) for item in cached["continuity_state_after"]
                    ][-self.CONTINUITY_MEMORY_LIMIT:]
                    completed_ids.update(ids)
                    states[current] = {
                        "status": "completed", "ids": ids,
                        "input_fingerprint": chunk_key,
                        "checkpoint": cached["path"].name,
                    }
                    project.translation_status = (
                        "completed" if len(completed_ids) == total_rows else "partial")
                    manager.save(project, directory)
                    if progress:
                        progress(f"Chunk {index}/{len(all_batches)} — cache HIT • "
                                 f"Bản dịch: {len(completed_ids)}/{total_rows} câu")
                    continue

                states[current] = {"status": "running", "ids": ids,
                                   "input_fingerprint": chunk_key}
                manager.save(project, directory)
                if progress:
                    progress(
                        f"Chunk {index}/{len(all_batches)} — đang gọi AI • evidence={evidence.mode} "
                        f"• window={evidence.window[0]:.1f}-{evidence.window[1]:.1f}s")
                prompt = translation_prompt(
                    project, targets, before, after, translated[-5:], evidence.diagnostic)
                response, cache_key = requests.request_multimodal(
                    EDITORIAL_RULES, prompt, evidence.media, TRANSLATION_SCHEMA,
                    lambda payload: validate_translation_response(payload, ids),
                    f"Dịch nhóm {index}/{len(all_batches)}", evidence.fingerprint)
                rows = response["translations"]
                check_cancel(cancel)
                memory_before_update = [dict(item) for item in project.translation_continuity_memory]
                self._update_continuity_memory(project, response.get("continuity_updates", []))
                continuity_after = [dict(item) for item in project.translation_continuity_memory]
                project.translation_continuity_memory = memory_before_update
                checkpoint_path = checkpoints.save(
                    chunk_key, ids, rows, continuity_after,
                    response.get("uncertainties", []), model, evidence.fingerprint,
                )
                translated.extend(rows)
                for row in rows:
                    apply_row(row)
                project.translation_continuity_memory = continuity_after
                if response.get("uncertainties"):
                    project.translation_notes[f"chunk:{index}:uncertainties"] = " | ".join(
                        response["uncertainties"][:20])
                completed_ids.update(ids)
                states[current] = {
                    "status": "completed", "ids": ids, "cache_key": cache_key,
                    "input_fingerprint": chunk_key, "checkpoint": checkpoint_path.name,
                }
                project.translation_status = (
                    "completed" if len(completed_ids) == total_rows else "partial")
                manager.save(project, directory)
                save_translation_artifacts(project, directory)
                log.info(
                    "[TRANSLATE CHECKPOINT] chunk=%s/%s target_ids=%s saved=true "
                    "translated_total=%s/%s",
                    index, len(all_batches), f"{ids[0]}-{ids[-1]}",
                    len(completed_ids), total_rows,
                )
            check_cancel(cancel)
            project.translation_status = "completed"
            project.cache_hashes["translation"] = fingerprint
            if [(row.id, row.start, row.end, row.zh) for row in project.segments] != canonical_before:
                raise ValueError("Translation không được thay đổi ID/Chinese/timestamp canonical")
            manager.save(project, directory)
        except Exception as exc:
            project.translation_status = (
                "partial" if isinstance(exc, CancelledError) else "failed_resumable")
            if current and states.get(current, {}).get("status") != "completed":
                states.setdefault(current, {"ids": current_ids})
                states[current]["status"] = (
                    "cancelled" if isinstance(exc, CancelledError) else "failed")
            manager.save(project, directory)
            save_translation_artifacts(project, directory)
            last_id = max(completed_ids) if completed_ids else None
            log.error(
                "[TRANSLATE ERROR] chunk=%s/%s target_ids=%s classification=%s "
                "http_status=%r retry_after=%r completed_before_failure=%s/%s resumable=true",
                current, len(all_batches),
                f"{current_ids[0]}-{current_ids[-1]}" if current_ids else "none",
                getattr(getattr(exc, "error_category", None), "value", "CANCELLED" if isinstance(exc, CancelledError) else "UNKNOWN"),
                getattr(exc, "status_code", None), getattr(exc, "retry_after_seconds", None),
                len(completed_ids), total_rows,
            )
            if isinstance(exc, AIProviderError):
                raise TranslationResumableError(
                    translation_failure_message(exc, len(completed_ids), total_rows, last_id), exc,
                ) from None
            if isinstance(exc, CancelledError):
                raise CancelledError(
                    f"Đã hủy. Đã lưu {len(completed_ids)}/{total_rows} câu; "
                    "bấm Dịch để tiếp tục.") from None
            raise
        finally:
            requests.close()
        save_translation_artifacts(project, directory)
        qa_ids = [segment.id for segment in project.segments if segment.id not in imported_ids]
        project, output_directory = self.qa_service.run(
            project, directory, ids=qa_ids, model=model, cancel=cancel, progress=progress,
        )
        if progress:
            progress(f"Đã dịch và QA/QC {len(translated)} dòng; xem Subtitle và subtitle/vi.srt")
        return project, output_directory
