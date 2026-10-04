"""Single source of truth for general OpenRouter model routing."""
from dataclasses import dataclass
import logging

from cartoon_sub.ai.openrouter_client import supports_capability


log = logging.getLogger(__name__)


class AIModelNotConfiguredError(ValueError):
    pass


@dataclass(frozen=True)
class AIModelResolution:
    model_id: str
    source: str
    feature: str
    tab: str
    required_capabilities: tuple


class AIModelResolver:
    """Resolve tab override -> application default, then validate the catalog."""

    def __init__(self, settings_store):
        self.store = settings_store

    def selected_model_id(self, tab, settings=None):
        settings = settings or self.store.load()
        overrides = settings.tab_model_overrides or {}
        custom = str(overrides.get(tab, "") or "").strip()
        return (custom, "TAB_OVERRIDE") if custom else (
            str(settings.default_ai_model or "").strip(), "GLOBAL_DEFAULT")

    def resolve(self, feature, tab, required_capabilities=None):
        capabilities = tuple(required_capabilities or ("text",))
        settings = self.store.load()
        model_id, source = self.selected_model_id(tab, settings)
        if not model_id:
            raise AIModelNotConfiguredError(
                "Chưa chọn Model AI mặc định.\n"
                "Hãy vào Settings → AI và chọn một model trước khi sử dụng chức năng AI."
            )
        cached = self.store.openrouter_catalog_cache()
        models = list((cached or {}).get("models", []))
        metadata = next((item for item in models if item.get("id") == model_id), None)
        if metadata is None:
            raise ValueError(
                f"Model '{model_id}' không tồn tại trong OpenRouter catalog hiện tại.\n"
                "Hãy chọn model khác cho tab này hoặc đổi Model AI mặc định."
            )
        labels = {"text": "văn bản", "vision_frames": "hình ảnh", "vision_video": "video"}
        for capability in capabilities:
            if not supports_capability(metadata, capability):
                raise ValueError(
                    f"Model '{model_id}' không hỗ trợ {labels.get(capability, capability)}.\n"
                    "Hãy chọn model khác cho tab này hoặc đổi Model AI mặc định."
                )
        log.info("[AI MODEL] feature=%s tab=%s effective_model=%s selection_source=%s "
                 "required_capabilities=%s", feature, tab, model_id, source,
                 ",".join(capabilities))
        return AIModelResolution(model_id, source, feature, tab, capabilities)
