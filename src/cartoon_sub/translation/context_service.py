import logging
from pathlib import Path
from cartoon_sub.ai.gemini_client import GeminiClient
from cartoon_sub.ai.text_client import model_metadata
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
        project = Project.from_dict(project.to_dict())
        settings = self.store.load()
        visual_client = None
        try:
            source = Path(project.source_video_path)
            if not source.is_absolute():
                source = Path(directory) / source
            source = source.resolve(strict=True)
            project.source_video_path = str(source)
            model = settings.translation_model if settings.translation_provider == "gemini" else settings.transcription_model
            capability = model_metadata("gemini", model)
            if capability is None or not capability["capabilities"].get("vision", False):
                raise ValueError(f"Model hiện tại không hỗ trợ phân tích video: {model}")
            logger.info(
                "[CONTEXT ANALYSIS] project=%s video_path=%s video_exists=%s video_size=%d "
                "transcript_rows=%d visual_cache=CHECK",
                project.name, source, source.is_file(), source.stat().st_size, len(project.segments),
            )
            if progress:
                progress("Context AI • ANALYZING_VIDEO")
            if self.factory is GeminiClient:
                visual_client = GeminiClient(self.store.get_gemini_keys(settings))
            else:
                credential = self.store.get_key("gemini") if hasattr(self.store, "get_key") else "test"
                visual_client = self.factory(credential)
            analyzer = VisualContextAnalyzer(visual_client, model)
            proposal = analyzer.analyze(
                project, directory, cancel=cancel, progress=progress,
            )
            check_cancel(cancel)
            if not proposal.get("visual_contexts"):
                raise ValueError("Gemini không trả Visual Context theo ID")
            if progress:
                progress("Context AI • MERGING_CONTEXT")
            project.context_proposal = proposal
            project.context_proposal_hash = source_fingerprint(project)
            project.context_proposal_config_hash = context_config_fingerprint(project)
            project.context_status = "proposal_ready"
            project.visual_context_status = "proposal_ready"
            project.visual_context_signature = visual_source_signature(project)
            project.visual_context_error = ""
            logger.info(
                "[CONTEXT ANALYSIS] project=%s visual_cache=%s visual_rows=%d status=READY",
                project.name, "HIT" if analyzer.cache_hits and not analyzer.cache_misses else "MISS",
                len(proposal["visual_contexts"]),
            )
            ProjectManager().save(project, directory)
            if progress:
                progress("Context AI • READY")
            return project, Path(directory)
        except CancelledError:
            raise
        except Exception as exc:
            reason = self._failure_reason(exc, project.source_video_path)
            logger.warning("[CONTEXT ANALYSIS] status=FAILED_VIDEO_CONTEXT reason=%s", reason)
            project.context_proposal = {}
            project.context_status = "VISUAL_CONTEXT_FAILED"
            project.visual_context_status = "VISUAL_CONTEXT_FAILED"
            project.visual_context_signature = ""
            project.visual_context_error = reason
            ProjectManager().save(project, directory)
            if progress:
                progress("Context AI • FAILED_VIDEO_CONTEXT")
            raise ValueError(
                "Chưa thể hoàn tất phân tích ngữ cảnh vì chưa đối chiếu được video.\n"
                f"Nguyên nhân: {reason}"
            ) from exc
        finally:
            if visual_client is not None:
                visual_client.close()

    @staticmethod
    def _failure_reason(exc, video_path):
        if isinstance(exc, FileNotFoundError):
            return f"Không tìm thấy video: {video_path}"
        text = str(exc).strip() or type(exc).__name__
        if "does not support" in text.casefold() or "không hỗ trợ" in text.casefold():
            return text
        if "ffmpeg" in text.casefold():
            first = next((line.strip() for line in text.splitlines() if line.strip()), text)
            return f"Không thể tạo video chunk: {first[:500]}"
        return text[:1000]
