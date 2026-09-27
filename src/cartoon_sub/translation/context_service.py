import json
import logging
from .context_models import StoryContext, CONTEXT_SCHEMA
from .chunker import batches, source_rows
from .prompts import CONTEXT_RULES, editorial
from .gemini_translator import validate_context
from .requests import CachedRequests
from cartoon_sub.ai.gemini_client import GeminiClient
from cartoon_sub.ai.text_client import text_client_factory
from cartoon_sub.project.cache import content_hash, check_cancel
from cartoon_sub.project.project_manager import ProjectManager
from cartoon_sub.subtitle.models import Project
from cartoon_sub.media.process import CancelledError
from .visual_context import VisualContextAnalyzer, visual_source_signature


logger = logging.getLogger(__name__)


def source_fingerprint(project):
    # Keep existing editorial profiles attached to their original text during schema migration.
    return content_hash([{"id":s.id,"start":s.start,"end":s.end,"speaker_id":s.speaker_id,"zh":s.zh}
                         for s in project.segments])


def context_config_fingerprint(project):
    """Identity of user-owned context constraints plus their source transcript."""
    return content_hash({
        "source": source_fingerprint(project),
        "genres": list(project.translation_genres),
        "primary_genre": project.translation_genres[0] if project.translation_genres else None,
        "translation_style": project.translation_preset,
        "proper_name_rule": project.proper_name_mode,
        "user_mappings": project.glossary,
        "supplemental_requirements": project.translation_prompt.strip(),
    })


class ContextService:
    def __init__(self, store, client_factory=GeminiClient):
        self.store, self.factory = store, client_factory

    def analyze(self, project, directory, *, cancel=None, progress=None):
        if not project.segments or any(not s.zh.strip() for s in project.segments):
            raise ValueError("Cần transcript tiếng Trung không rỗng để phân tích")
        from pathlib import Path
        project = Project.from_dict(project.to_dict())
        settings = self.store.load()
        visual_client = None
        try:
            # Resolve before constructing a network client.  Legacy/text-only
            # projects with a missing source must retain the cheap cached fallback.
            Path(project.source_video_path).resolve(strict=True)
            if self.factory is GeminiClient:
                visual_client = GeminiClient(self.store.get_gemini_keys(settings))
            else:
                credential = self.store.get_key("gemini") if hasattr(self.store, "get_key") else "test"
                visual_client = self.factory(credential)
            model = settings.translation_model if settings.translation_provider == "gemini" else settings.transcription_model
            proposal = VisualContextAnalyzer(visual_client, model).analyze(
                project, directory, cancel=cancel, progress=progress,
            )
            check_cancel(cancel)
            project.context_proposal = proposal
            project.context_proposal_hash = source_fingerprint(project)
            project.context_proposal_config_hash = context_config_fingerprint(project)
            project.context_status = "proposal_ready"
            project.visual_context_status = "proposal_ready"
            project.visual_context_signature = visual_source_signature(project)
            ProjectManager().save(project, directory)
            return project, Path(directory)
        except CancelledError:
            raise
        except Exception as exc:
            logger.warning("[VISUAL CONTEXT] unavailable; transcript-only candidate fallback: %s", exc)
            project.visual_context_status = "VISUAL_CONTEXT_UNAVAILABLE"
            project.visual_context_signature = ""
            if progress:
                progress("[VISUAL CONTEXT] VISUAL_CONTEXT_UNAVAILABLE • dùng transcript context và đánh dấu cần duyệt")
        finally:
            if visual_client is not None:
                visual_client.close()

        # Safe fallback: keep translation usable, but never pretend video was analyzed.
        factory = self.factory if self.factory is not GeminiClient else (GeminiClient if settings.translation_provider == "gemini" else text_client_factory(settings.translation_provider))
        requests = CachedRequests(self.store, Path(directory) / "cache" / "context", settings.translation_model,
                                  settings.retry_count, cancel, progress, factory, settings.translation_provider)
        proposal = StoryContext().to_dict()
        all_batches = list(batches(source_rows(project.segments), 100))
        seen = []
        try:
            for index, rows in enumerate(all_batches, 1):
                seen.extend(r["id"] for r in rows)
                prompt = json.dumps({"editorial": editorial(project), "previous_proposal": proposal,
                    "batch": index, "total_batches": len(all_batches), "transcript": rows}, ensure_ascii=False)
                proposal, _ = requests.request(CONTEXT_RULES, prompt, CONTEXT_SCHEMA,
                    lambda payload: validate_context(payload, seen), f"Phân tích ngữ cảnh {index}/{len(all_batches)}")
            check_cancel(cancel)
            project.context_proposal = proposal
            project.context_proposal_hash = source_fingerprint(project)
            project.context_proposal_config_hash = context_config_fingerprint(project)
            project.context_status = "proposal_ready"
            project.visual_context_status = "VISUAL_CONTEXT_UNAVAILABLE"
            project.context_proposal.setdefault("uncertainties", []).append(
                "VISUAL_CONTEXT_UNAVAILABLE: Chưa đối chiếu được video; đại từ/người nói/người được nhắc tới cần duyệt."
            )
            ProjectManager().save(project, directory)
            return project, Path(directory)
        finally:
            requests.close()
