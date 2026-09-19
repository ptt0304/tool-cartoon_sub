"""A lazy client and durable request cache shared by context analysis and translation."""
import json
from pathlib import Path
from threading import Event
from cartoon_sub.ai.gemini_client import GeminiClient, GeminiError
from cartoon_sub.project.cache import atomic_json, content_hash, check_cancel
from .gemini_translator import TranslationValidationError
from .prompts import PROMPT_VERSION


class CachedRequests:
    def __init__(self, store, directory, model, retries, cancel=None, progress=None, client_factory=GeminiClient, provider="gemini"):
        self.store, self.directory, self.model = store, Path(directory), model
        self.retries, self.cancel = retries, cancel or Event()
        self.report, self.factory, self.provider = progress or (lambda text: None), client_factory, provider
        self.client = None

    def close(self):
        if self.client:
            self.client.close()

    def request(self, system, prompt, schema, validate, label):
        check_cancel(self.cancel)
        key = content_hash({"version": PROMPT_VERSION, "provider": self.provider, "model": self.model, "system": system,
                            "prompt": prompt, "schema": schema})
        path = self.directory / f"{key}.json"
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("status") == "completed":
                    value = validate(data["response"])
                    self.report(f"{label}: dùng cache")
                    return value, key
            except (ValueError, TypeError, KeyError, AttributeError, GeminiError):
                pass
        if self.client is None:
            self.client = self.factory(self.store.get_key(self.provider))
        correction = ""
        for attempt in range(self.retries + 1):
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
                atomic_json(path, {"status": "failed", "attempt": attempt + 1, "error": str(exc)})
                if not exc.retryable or attempt >= self.retries:
                    raise
                if isinstance(exc, TranslationValidationError):
                    correction = "\nLần trước sai hợp đồng đầu ra: " + str(exc) + ". Trả lại đầy đủ theo đúng schema và ID."
                if self.cancel.wait(min(2 ** (attempt + 1), 30)):
                    check_cancel(self.cancel)
