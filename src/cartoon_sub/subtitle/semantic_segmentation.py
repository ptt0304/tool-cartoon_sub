"""Narrow, text-preserving Gemini fallback for unresolved subtitle boundaries."""
import json
import logging

from cartoon_sub.ai.gemini_client import GeminiClient, GeminiError
from cartoon_sub.ai.text_client import text_client_factory
from cartoon_sub.subtitle.segmentation import restore_exact_parts


log = logging.getLogger(__name__)


SEMANTIC_SEGMENTATION_SCHEMA = {
    "type": "OBJECT",
    "properties": {"parts": {"type": "ARRAY", "items": {"type": "STRING"}}},
    "required": ["parts"],
}
SEMANTIC_SEGMENTATION_RULES = (
    "You identify Vietnamese subtitle split boundaries only. Return JSON with parts. "
    "Each part must be an exact consecutive substring of the supplied text. Do not translate, "
    "paraphrase, correct, add, remove, reorder, or normalize words or punctuation."
)
SEMANTIC_SEGMENTATION_VERSION = "semantic-split-v1"


class SemanticSegmentationValidationError(GeminiError):
    """The response did not honour the source-text-only contract."""


def validate_parts(payload, original_text, max_segments):
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            raise SemanticSegmentationValidationError("Gemini semantic split không phải JSON hợp lệ") from None
    if not isinstance(payload, dict) or set(payload) != {"parts"} or not isinstance(payload["parts"], list):
        raise SemanticSegmentationValidationError("Gemini semantic split phải chỉ chứa parts")
    parts = payload["parts"]
    if not 2 <= len(parts) <= max_segments or any(not isinstance(part, str) or not part.strip() for part in parts):
        raise SemanticSegmentationValidationError("Gemini semantic split có số phần hoặc nội dung không hợp lệ")
    try:
        return restore_exact_parts(parts, original_text)
    except ValueError as exc:
        raise SemanticSegmentationValidationError(f"Gemini semantic split đã thay đổi văn bản nguồn: {exc}") from None


class SemanticSegmentationService:
    """Calls Gemini only after the local engine reports no safe semantic boundary."""
    def __init__(self, store, client_factory=GeminiClient):
        self.store, self.factory = store, client_factory

    def cache_identity(self):
        settings=self.store.load()
        model=(settings.tab_model_overrides or {}).get("subtitle") or settings.default_ai_model
        return {"version": SEMANTIC_SEGMENTATION_VERSION, "provider": "openrouter", "model": model}

    def split(self, text, language="vi", target_syllables=12, max_syllables=18, max_segments=4, *, cancel=None):
        if language != "vi" or not isinstance(text, str) or not text.strip():
            raise ValueError("Semantic fallback chỉ nhận subtitle tiếng Việt không rỗng")
        if type(target_syllables) is not int or type(max_syllables) is not int or type(max_segments) is not int:
            raise ValueError("Giới hạn semantic fallback không hợp lệ")
        if not 1 <= target_syllables <= max_syllables or not 2 <= max_segments <= 8:
            raise ValueError("Giới hạn semantic fallback không hợp lệ")
        settings = self.store.load()
        from cartoon_sub.ai.model_resolver import AIModelResolver
        model = AIModelResolver(self.store).resolve(
            "SUBTITLE_SEMANTIC_SEGMENTATION", "subtitle", ("text",)).model_id
        cached = self.store.openrouter_catalog_cache()
        factory=(self.factory if self.factory is not GeminiClient else
                 text_client_factory(settings.translation_provider, cached["models"] if cached else None))
        credentials = (self.store.openrouter_key_pool()
                       if settings.translation_provider == "openrouter"
                       else self.store.get_key(settings.translation_provider))
        client = factory(credentials)
        try:
            prompt = json.dumps({"text": text, "language": language, "preferred_syllables": target_syllables,
                "max_syllables": max_syllables, "max_segments": max_segments}, ensure_ascii=False)
            payload = client.generate_json(SEMANTIC_SEGMENTATION_RULES, prompt,
                SEMANTIC_SEGMENTATION_SCHEMA, model, cancel=cancel)
            log.info("[TIMING] semantic raw_response=%r", payload)
            try:
                parts = validate_parts(payload, text, max_segments)
            except SemanticSegmentationValidationError as exc:
                log.warning("[TIMING] semantic rejected=%s", exc)
                raise
            log.info("[TIMING] semantic accepted=%r", parts)
            return parts
        finally:
            client.close()
