from pathlib import Path
from .chunker import translation_batches, source_rows
from .context_models import StoryContext
from .context_service import source_fingerprint
from .prompts import EDITORIAL_RULES, editorial, translation_prompt, PROMPT_VERSION
from cartoon_sub.speaker.service import refresh_timeline, review_complete
from .gemini_translator import TRANSLATION_SCHEMA, validate_translation
from .requests import CachedRequests
from .artifacts import save_translation_artifacts
from cartoon_sub.ai.gemini_client import GeminiClient
from cartoon_sub.ai.text_client import text_client_factory
from cartoon_sub.project.cache import check_cancel, content_hash
from cartoon_sub.project.project_manager import ProjectManager
from cartoon_sub.subtitle.models import Project
from cartoon_sub.media.process import CancelledError


def translation_fingerprint(project, settings):
    return content_hash({"source": source_rows(project.segments), "editorial": editorial(project),
                        "system": EDITORIAL_RULES, "provider": settings.translation_provider, "model": settings.translation_model,
                        "chunk_size": settings.translation_chunk_size, "version": PROMPT_VERSION,
                        "dubbing_settings":project.dubbing_settings})


def mark_stale(project, settings):
    previous = project.cache_hashes.get("translation")
    if previous and previous != translation_fingerprint(project, settings):
        project.translation_status = "stale"


class TranslationPipeline:
    def __init__(self, store, client_factory=GeminiClient):
        self.store, self.factory = store, client_factory

    def run(self, project, directory, *, cancel=None, progress=None):
        refresh_timeline(project)
        if not review_complete(project):
            raise ValueError("Hãy gán và xác nhận speaker trong Transcript trước khi dịch")
        if not project.segments or any(not s.zh.strip() for s in project.segments):
            raise ValueError("Cần transcript tiếng Trung không rỗng để dịch")
        if project.context_source_hash != source_fingerprint(project):
            raise ValueError("Hãy mở Hồ sơ đang áp dụng, kiểm tra và bấm Áp dụng hồ sơ cho transcript hiện tại")
        StoryContext.from_dict(project.story_context, {s.id for s in project.segments})
        project = Project.from_dict(project.to_dict())
        settings = self.store.load()
        all_batches = list(translation_batches(project.segments, settings.translation_chunk_size))
        manager = ProjectManager()
        fingerprint = translation_fingerprint(project, settings)
        old_run = project.cache_hashes.get("translation")
        if project.cache_hashes.get("translation_run") != fingerprint:
            # Previous output remains on disk until its replacement is ready; old chunks cannot be mixed in.
            project.chunk_states["translation"] = {}
        states = project.chunk_states.setdefault("translation", {})
        project.cache_hashes["translation_run"] = fingerprint
        # Keep the output fingerprint pointing at old data throughout a replacement run,
        # including across a process crash. Only first-run partial output gets the new identity.
        if old_run is None:
            project.cache_hashes["translation"] = fingerprint
        project.translation_status = "running"
        manager.save(project, directory)
        factory = self.factory if self.factory is not GeminiClient else (GeminiClient if settings.translation_provider == "gemini" else text_client_factory(settings.translation_provider))
        requests = CachedRequests(self.store, Path(directory) / "cache" / "translation", settings.translation_model,
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
        try:
            for index, (targets, before, after) in enumerate(all_batches, 1):
                current = str(index)
                ids = [r["id"] for r in targets]
                states[current] = {"status": "running", "ids": ids}
                manager.save(project, directory)
                prompt = translation_prompt(project, targets, before, after, translated[-5:])
                rows, cache_key = requests.request(EDITORIAL_RULES, prompt, TRANSLATION_SCHEMA,
                    lambda payload: validate_translation(payload, ids), f"Dịch nhóm {index}/{len(all_batches)}")
                check_cancel(cancel)
                translated.extend(rows)
                states[current] = {"status": "completed", "ids": ids, "cache_key": cache_key}
                # Persist chunks on the first run/resume with the SAME editorial fingerprint.
                # Changed editorial settings commit the entire replacement only after success.
                if old_run in (None, fingerprint):
                    for row in rows:
                        apply_row(row)
                manager.save(project, directory)
            check_cancel(cancel)
            for row in translated:
                apply_row(row)
            project.translation_status = "completed"
            project.cache_hashes["translation"] = fingerprint
            project.selected_models["translation"] = settings.translation_model
            manager.save(project, directory)
        except Exception as exc:
            project.translation_status = "cancelled" if isinstance(exc, CancelledError) else "failed"
            if current and states[current]["status"] != "completed":
                states[current]["status"] = project.translation_status
            # Restore identity of old output after a failed replacement run.
            if old_run not in (None, fingerprint):
                project.cache_hashes["translation"] = old_run
                project.translation_status = "stale"
            manager.save(project, directory)
            raise
        finally:
            requests.close()
        save_translation_artifacts(project, directory)
        if progress:
            progress(f"Đã dịch {len(translated)} dòng; xem Subtitle và subtitle/vi.srt")
        return project, Path(directory)
