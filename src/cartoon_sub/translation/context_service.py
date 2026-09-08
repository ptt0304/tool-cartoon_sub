import json
from .context_models import StoryContext, CONTEXT_SCHEMA
from .chunker import batches, source_rows
from .prompts import CONTEXT_RULES, editorial
from .gemini_translator import validate_context
from .requests import CachedRequests
from cartoon_sub.ai.gemini_client import GeminiClient
from cartoon_sub.project.cache import content_hash, check_cancel
from cartoon_sub.project.project_manager import ProjectManager
from cartoon_sub.subtitle.models import Project


def source_fingerprint(project):
    # Keep existing editorial profiles attached to their original text during schema migration.
    return content_hash([{"id":s.id,"zh":s.zh} for s in project.segments])


class ContextService:
    def __init__(self, store, client_factory=GeminiClient):
        self.store, self.factory = store, client_factory

    def analyze(self, project, directory, *, cancel=None, progress=None):
        if not project.segments or any(not s.zh.strip() for s in project.segments):
            raise ValueError("Cần transcript tiếng Trung không rỗng để phân tích")
        from pathlib import Path
        project = Project.from_dict(project.to_dict())
        settings = self.store.load()
        requests = CachedRequests(self.store, Path(directory) / "cache" / "context", settings.translation_model,
                                  settings.retry_count, cancel, progress, self.factory)
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
            project.context_status = "proposal_ready"
            ProjectManager().save(project, directory)
            return project, Path(directory)
        finally:
            requests.close()
