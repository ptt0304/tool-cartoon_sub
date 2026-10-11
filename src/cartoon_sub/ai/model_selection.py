from dataclasses import dataclass
from cartoon_sub.ai.runtime_debug import ai_debug


_LEGACY_OPENROUTER_MODELS = {
    "gemini-2.5-flash": "google/gemini-2.5-flash",
    "gemini-2.5-flash-lite": "google/gemini-2.5-flash-lite",
    "gemini-2.5-pro": "google/gemini-2.5-pro",
    "gemini-3.5-flash": "google/gemini-3.5-flash",
    "gemini-3.5-flash-lite": "google/gemini-3.5-flash-lite",
    "gemini-3.5-transcribe": "google/gemini-3.5-transcribe",
    "gemini-3.6-flash": "google/gemini-3.6-flash",
    "gemini-3.7-flash": "google/gemini-3.7-flash",
    "gemini-3.8-flash": "google/gemini-3.8-flash",
}


def canonical_openrouter_model_id(model_id):
    """Migrate known direct-Gemini IDs; all runtime selections use OpenRouter."""
    raw = str(model_id or "").strip()
    return _LEGACY_OPENROUTER_MODELS.get(raw, raw)

@dataclass(frozen=True)
class AIModelSelection:
    provider: str
    model_id: str
    def to_dict(self): return {"provider": self.provider, "model_id": self.model_id}
    @classmethod
    def from_value(cls, value, legacy_provider="openrouter"):
        if isinstance(value, cls):
            ai_debug("AIModelSelection.from_value", raw=value.to_dict(), raw_type=type(value).__name__,
                     provider=value.provider, model_id=value.model_id, selection_type=type(value).__name__)
            return cls("openrouter", canonical_openrouter_model_id(value.model_id))
        if isinstance(value, dict):
            selection = cls("openrouter", canonical_openrouter_model_id(
                value.get("model_id") or value.get("id") or ""))
            ai_debug("AIModelSelection.from_value", raw=value, raw_type=type(value).__name__,
                     provider=selection.provider, model_id=selection.model_id,
                     selection_type=type(selection).__name__)
            return selection
        selection = cls("openrouter", canonical_openrouter_model_id(value))
        ai_debug("AIModelSelection.from_value", raw=value, raw_type=type(value).__name__,
                 provider=selection.provider, model_id=selection.model_id,
                 selection_type=type(selection).__name__)
        return selection
