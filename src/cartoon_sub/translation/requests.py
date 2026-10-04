"""A lazy client and durable request cache shared by context analysis and translation."""
import json
from pathlib import Path
from threading import Event
from cartoon_sub.ai.gemini_client import GeminiClient, GeminiError
from cartoon_sub.ai.language_contract import USER_FACING_AI_CORRECTION, UserFacingLanguageError
from cartoon_sub.project.cache import atomic_json, content_hash, check_cancel
from .gemini_translator import TranslationValidationError
from .prompts import PROMPT_VERSION


def _safe_error_record(exc, attempt, evidence_fingerprint=None):
    """Persist useful provider diagnostics without credentials or request headers."""
    category = getattr(exc, "error_category", None)
    value = {
        "status": "failed",
        "attempt": attempt,
        "classification": getattr(category, "value", str(category or "UNKNOWN")),
        "http_status": getattr(exc, "status_code", None),
        "provider_code": getattr(exc, "provider_code", None),
        "provider_message": getattr(exc, "provider_message", None),
        "retry_after": getattr(exc, "retry_after_seconds", None),
        "error": str(exc)[:500],
    }
    if evidence_fingerprint is not None:
        value["evidence"] = evidence_fingerprint
    return value


class CachedRequests:
    def __init__(self, store, directory, model, retries, cancel=None, progress=None, client_factory=GeminiClient, provider="gemini"):
        self.store, self.directory, self.model = store, Path(directory), model
        self.retries, self.cancel = retries, cancel or Event()
        self.report, self.factory, self.provider = progress or (lambda text: None), client_factory, provider
        self.client = None

    def close(self):
        if self.client:
            self.client.close()

    def _ensure_client(self):
        if self.client is None:
            credential = (self.store.get_gemini_keys() if self.provider == "gemini" and self.factory is GeminiClient
                          else self.store.openrouter_key_pool() if self.provider == "openrouter"
                          else self.store.get_key(self.provider))
            self.client = self.factory(credential)
            if self.provider == "openrouter" and not getattr(self.client, "models", None):
                cached = self.store.openrouter_catalog_cache()
                if cached:
                    self.client.models = {model["id"]: model for model in cached["models"]}
        return self.client

    def request(self, system, prompt, schema, validate, label, *, force=False):
        check_cancel(self.cancel)
        key = content_hash({"version": PROMPT_VERSION, "provider": self.provider, "model": self.model, "system": system,
                            "prompt": prompt, "schema": schema})
        path = self.directory / f"{key}.json"
        if path.exists() and not force:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("status") == "completed":
                    value = validate(data["response"])
                    self.report(f"{label}: dùng cache")
                    return value, key
            except (ValueError, TypeError, KeyError, AttributeError, GeminiError):
                pass
        self._ensure_client()
        correction = ""
        language_corrections = 0
        for attempt in range(self.retries + 2):
            check_cancel(self.cancel)
            self.report(f"{label}: gọi AI, lần {attempt + 1}/{self.retries + 1}")
            atomic_json(path, {"status": "running", "attempt": attempt + 1})
            try:
                payload = self.client.generate_json(system, prompt + correction, schema, self.model, cancel=self.cancel)
                check_cancel(self.cancel)
                value = validate(payload)
                atomic_json(path, {"status": "completed", "response": payload})
                return value, key
            except GeminiError as exc:
                atomic_json(path, _safe_error_record(exc, attempt + 1))
                if isinstance(exc, UserFacingLanguageError):
                    if language_corrections >= 1:
                        raise
                    language_corrections += 1
                    correction = "\n" + USER_FACING_AI_CORRECTION
                    continue
                if not exc.retryable or attempt >= self.retries:
                    raise
                if isinstance(exc, TranslationValidationError):
                    correction = "\nLần trước sai hợp đồng đầu ra: " + str(exc) + ". Trả lại đầy đủ theo đúng schema và ID."
                if self.cancel.wait(min(2 ** (attempt + 1), 30)):
                    check_cancel(self.cancel)

    def request_multimodal(self, system, prompt, media, schema, validate, label,
                           evidence_fingerprint, *, force=False):
        """Cache a multimodal request by evidence identity, never by credentials."""
        if not media:
            return self.request(system, prompt, schema, validate, label, force=force)
        check_cancel(self.cancel)
        key = content_hash({
            "version": PROMPT_VERSION, "provider": self.provider, "model": self.model,
            "system": system, "prompt": prompt, "schema": schema,
            "evidence": evidence_fingerprint,
        })
        path = self.directory / f"{key}.json"
        if path.exists() and not force:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("status") == "completed":
                    value = validate(data["response"])
                    self.report(f"{label}: dùng cache audiovisual")
                    return value, key
            except (ValueError, TypeError, KeyError, AttributeError, GeminiError):
                pass
        client = self._ensure_client()
        if not hasattr(client, "generate_multimodal_json"):
            raise ValueError("Model/client hiện tại không hỗ trợ request audiovisual")
        correction = ""
        language_corrections = 0
        for attempt in range(self.retries + 2):
            check_cancel(self.cancel)
            self.report(f"{label}: gọi AI audiovisual, lần {attempt + 1}/{self.retries + 1}")
            atomic_json(path, {"status": "running", "attempt": attempt + 1,
                               "evidence": evidence_fingerprint})
            try:
                payload = client.generate_multimodal_json(
                    system, prompt + correction, media, schema, self.model,
                    cancel=self.cancel, progress=self.report,
                )
                check_cancel(self.cancel)
                value = validate(payload)
                atomic_json(path, {"status": "completed", "response": payload,
                                   "evidence": evidence_fingerprint})
                return value, key
            except GeminiError as exc:
                atomic_json(path, _safe_error_record(
                    exc, attempt + 1, evidence_fingerprint))
                if isinstance(exc, UserFacingLanguageError):
                    if language_corrections >= 1:
                        raise
                    language_corrections += 1
                    correction = "\n" + USER_FACING_AI_CORRECTION
                    continue
                if not exc.retryable or attempt >= self.retries:
                    raise
                if isinstance(exc, TranslationValidationError):
                    correction = ("\nLần trước sai hợp đồng đầu ra: " + str(exc)
                                  + ". Trả lại đầy đủ theo đúng schema và ID.")
                if self.cancel.wait(min(2 ** (attempt + 1), 30)):
                    check_cancel(self.cancel)
