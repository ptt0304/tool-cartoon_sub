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
        overrides = getattr(settings, "tab_model_overrides", None) or {}
        raw = overrides.get(tab)
        if hasattr(settings, "tab_selection"):
            selection = settings.tab_selection(tab)
        else:
            from cartoon_sub.ai.model_selection import AIModelSelection
            fallback = (getattr(settings, "transcription_model", "") if tab == "transcript_stt"
                        else getattr(settings, "default_ai_model", ""))
            selection = AIModelSelection.from_value(raw or fallback)
        source = "TAB_OVERRIDE" if raw else "GLOBAL_DEFAULT"
        from cartoon_sub.ai.runtime_debug import ai_debug
        ai_debug("AIModelResolver.selected_model_id", tab=tab, raw_override=raw,
                 raw_override_type=type(raw).__name__, normalized_selection=selection.to_dict(),
                 provider=selection.provider, model_id=selection.model_id,
                 selection_type=type(selection).__name__, source=source)
        return selection, source

    def resolve(self, feature, tab, required_capabilities=None):
        capabilities = tuple(required_capabilities or ("text",))
        settings = self.store.load()
        selection, source = self.selected_model_id(tab, settings)
        from cartoon_sub.ai.runtime_debug import ai_debug
        ai_debug("AIModelResolver.resolve", feature=feature, tab=tab, provider=selection.provider,
                 model_id=selection.model_id, selection_type=type(selection).__name__, source=source)
        if selection.provider != "openrouter":
            ai_debug("ERROR", warning="Gemini/native selection entered OpenRouter resolver",
                     provider=selection.provider, model_id=selection.model_id)
            raise ValueError(
                f"Provider '{selection.provider}' phải được route trực tiếp, không qua OpenRouter resolver.")
        model_id = selection.model_id
        if not model_id:
            raise AIModelNotConfiguredError(
                "Chưa chọn Model AI mặc định.\n"
                "Hãy vào Settings → AI và chọn một model trước khi sử dụng chức năng AI."
            )
        cached = self.store.openrouter_catalog_cache()
        models = list((cached or {}).get("models", []))
        metadata = next((item for item in models if item.get("id") == model_id), None)
        if metadata is None:
            ai_debug("OpenRouter catalog error", input_value=model_id,
                     input_type=type(model_id).__name__, provider=selection.provider,
                     model_id=model_id)
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
