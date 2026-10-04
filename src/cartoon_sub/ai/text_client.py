"""Text-only JSON clients for translation/context providers."""
import json
import httpx
from cartoon_sub.ai.gemini_client import GeminiError
from cartoon_sub.project.cache import check_cancel


def _model(model_id, display_name=None, *, text=False, transcription=False, vision=False, status="stable"):
    return {"id": model_id, "display_name": display_name or model_id,
            "capabilities": {"text": text, "transcription": transcription, "vision": vision},
            "status": status}


# Audited against each provider's official public API/model documentation on 2026-09-24.
# Stable models precede preview models so obsolete saved selections get a safe stable fallback.
PROVIDER_CATALOG = {
    "gemini": {"name": "Google Gemini", "key_name": "gemini_key", "url": None, "models": (
        _model("gemini-3.8-flash", text=True, transcription=True, vision=True),
        _model("gemini-3.7-flash", text=True, transcription=True, vision=True),
        _model("gemini-3.6-flash", text=True, transcription=True, vision=True),
        _model("gemini-3.5-flash", text=True, transcription=True, vision=True),
        _model("gemini-3.5-flash-lite", text=True, transcription=True, vision=True),
        _model("gemini-3.5-transcribe", transcription=True),
        _model("gemini-3.1-flash-lite", text=True, vision=True),
        _model("gemini-3.1-pro-preview", text=True, transcription=True, vision=True, status="preview"),
        _model("gemini-3-flash-preview", text=True, transcription=True, vision=True, status="preview"),
        _model("gemini-2.5-pro", text=True, transcription=True, vision=True),
        _model("gemini-2.5-flash", text=True, transcription=True, vision=True),
        _model("gemini-2.5-flash-lite", text=True, transcription=True, vision=True),
    )},
    "openai": {"name": "OpenAI", "key_name": "openai_key", "url": "https://api.openai.com/v1/chat/completions",
               "models_url": "https://api.openai.com/v1/models", "models": (
        _model("gpt-5.6-sol", text=True, vision=True),
        _model("gpt-5.6-terra", text=True, vision=True),
        _model("gpt-5.6-luna", text=True, vision=True),
        _model("gpt-4o-transcribe", transcription=True),
        _model("gpt-4o-mini-transcribe", transcription=True),
    )},
    "anthropic": {"name": "Anthropic Claude", "key_name": "claude_key", "url": "https://api.anthropic.com/v1/messages", "models": (
        _model("claude-sonnet-5", text=True, vision=True),
        _model("claude-opus-5", text=True, vision=True),
        _model("claude-fable-5", text=True, vision=True),
    )},
    # Models are loaded dynamically from OpenRouter's /models endpoint.
    "openrouter": {"name": "OpenRouter", "key_name": "openrouter_key",
                   "url": "https://openrouter.ai/api/v1/chat/completions", "models": ()},
    "deepseek": {"name": "DeepSeek", "key_name": "deepseek_key", "url": "https://api.deepseek.com/chat/completions", "models": (
        _model("deepseek-v4-pro", text=True),
        _model("deepseek-flash", text=True, vision=True),
    )},
    "groq": {"name": "Groq", "key_name": "groq_key", "url": "https://api.groq.com/openai/v1/chat/completions",
             "models_url": "https://api.groq.com/openai/v1/models", "models": (
        _model("openai/gpt-oss-120b", text=True),
        _model("openai/gpt-oss-20b", text=True),
        _model("qwen/qwen3.8-27b", text=True, status="preview"),
        _model("whisper-large-v3", transcription=True),
        _model("whisper-large-v3-turbo", transcription=True),
    )},
    "mistral": {"name": "Mistral AI", "key_name": "mistral_key", "url": "https://api.mistral.ai/v1/chat/completions",
                "models_url": "https://api.mistral.ai/v1/models", "models": (
        _model("mistral-medium-3-5", text=True, vision=True),
        _model("mistral-small-2603", text=True, vision=True),
        _model("voxtral-mini-2602", transcription=True),
    )},
    "together": {"name": "Together AI", "key_name": "together_key", "url": "https://api.together.xyz/v1/chat/completions",
                 "models_url": "https://api.together.xyz/v1/models", "models": (
        _model("moonshotai/Kimi-K3", text=True),
        _model("openai/gpt-oss-120b", text=True),
        _model("deepseek-ai/DeepSeek-V4.1-Flash", text=True),
        _model("openai/whisper-large-v3", transcription=True),
    )},
    "fireworks": {"name": "Fireworks AI", "key_name": "fireworks_key", "url": "https://api.fireworks.ai/inference/v1/chat/completions", "models": (
        _model("accounts/fireworks/models/deepseek-v4-pro-0813", text=True),
        _model("accounts/fireworks/models/deepseek-v4-flash-0731", text=True),
        _model("accounts/fireworks/models/gpt-oss-120b", text=True),
        _model("accounts/fireworks/models/kimi-k3", text=True),
    )},
    "xai": {"name": "xAI Grok", "key_name": "xai_key", "url": "https://api.x.ai/v1/chat/completions",
            "models_url": "https://api.x.ai/v1/models", "models": (
        _model("grok-4.7", text=True, vision=True),
        _model("grok-voice-transcribe-2.0", transcription=True),
    )},
}

for _provider in PROVIDER_CATALOG.values():
    _provider["capabilities"] = tuple(capability for capability in ("text", "transcription", "vision")
                                           if any(model["capabilities"][capability]
                                                  for model in _provider["models"]))

PROVIDERS = {provider: (item["name"], item["url"], next(model["id"] for model in item["models"]
                                                         if model["capabilities"]["text"]))
             for provider, item in PROVIDER_CATALOG.items() if provider not in ("gemini", "openrouter")}
MODEL_PRESETS = {provider: tuple(model["id"] for model in item["models"])
                 for provider, item in PROVIDER_CATALOG.items()}


def provider_label(provider):
    return PROVIDER_CATALOG[provider]["name"]


def provider_models(provider, capability=None):
    return tuple(model["id"] for model in PROVIDER_CATALOG[provider]["models"]
                 if capability is None or model["capabilities"].get(capability, False))


def model_metadata(provider, model_id):
    return next((model for model in PROVIDER_CATALOG[provider]["models"] if model["id"] == model_id), None)


class TextProviderClient:
    def __init__(self, provider, api_key):
        if provider not in PROVIDERS:
            raise ValueError("Provider dịch không hợp lệ")
        self.provider, self.api_key = provider, api_key
        self.client = httpx.Client(timeout=120)

    def close(self):
        self.client.close()

    def _request(self, system, prompt, model):
        label, url, _ = PROVIDERS[self.provider]
        try:
            if self.provider == "anthropic":
                response = self.client.post(url, headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01"},
                    json={"model": model, "max_tokens": 16384, "system": system,
                          "messages": [{"role": "user", "content": prompt}]})
                response.raise_for_status(); return response.json()["content"][0]["text"]
            response = self.client.post(url, headers={"Authorization": f"Bearer {self.api_key}"},
                json={"model": model, "temperature": .3, "response_format": {"type": "json_object"},
                      "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]})
            response.raise_for_status(); return response.json()["choices"][0]["message"]["content"]
        except httpx.HTTPStatusError as exc:
            code=exc.response.status_code;retryable=code == 429 or code >= 500
            raise GeminiError(f"{label} HTTP {code}: kiểm tra API key, model, quota và quyền truy cập.",retryable) from None
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError):
            raise GeminiError(f"{label} không trả dữ liệu hợp lệ. Kiểm tra mạng, model và API key.",True) from None

    def generate_json(self, system, prompt, schema, model, cancel=None):
        check_cancel(cancel)
        contract="\nChỉ trả về một JSON object hợp lệ, không markdown. JSON phải tuân theo schema sau: " + json.dumps(schema, ensure_ascii=False)
        text=self._request(system,prompt+contract,model);check_cancel(cancel)
        try:return json.loads(text)
        except json.JSONDecodeError:raise GeminiError(f"{provider_label(self.provider)} không trả JSON hợp lệ.") from None

    def test_connection(self, model, cancel=None, progress=None):
        if progress:progress(f"Đang kiểm tra {provider_label(self.provider)}…")
        check_cancel(cancel);self._request("Reply with JSON only.", "Return {\"ok\":true}.", model);check_cancel(cancel)
        return f"Kết nối {provider_label(self.provider)} thành công; đã gửi một request thử nhỏ."


def text_client_factory(provider, models=None):
    if provider == "openrouter":
        from cartoon_sub.ai.openrouter_client import openrouter_client_factory
        return openrouter_client_factory(models)
    raise ValueError(
        "Cấu hình AI text cũ chưa được ánh xạ sang OpenRouter; "
        "hãy mở Settings > AI và chọn model từ catalog đã đồng bộ."
    )


class ProviderModelClient:
    """Lightweight availability check for non-Gemini transcription model IDs."""
    def __init__(self, provider, api_key):
        item = PROVIDER_CATALOG.get(provider, {})
        if not item.get("models_url"):
            raise ValueError(f"{provider_label(provider)} không có Models API hỗ trợ kiểm tra model.")
        self.provider, self.api_key = provider, api_key
        self.client = httpx.Client(timeout=15)

    def close(self):
        self.client.close()

    def test_connection(self, model, cancel=None, progress=None):
        if progress:
            progress(f"Đang kiểm tra model {provider_label(self.provider)}…")
        check_cancel(cancel)
        try:
            response = self.client.get(PROVIDER_CATALOG[self.provider]["models_url"],
                                       headers={"Authorization": f"Bearer {self.api_key}"})
            response.raise_for_status()
            rows = response.json().get("data", [])
            model_ids = {row.get("id") for row in rows if isinstance(row, dict)}
        except httpx.HTTPStatusError as exc:
            raise GeminiError(f"{provider_label(self.provider)} HTTP {exc.response.status_code}: "
                              "không kiểm tra được danh sách model.", exc.response.status_code >= 500) from None
        except (httpx.HTTPError, ValueError, TypeError, AttributeError):
            raise GeminiError(f"{provider_label(self.provider)} không trả danh sách model hợp lệ.", True) from None
        check_cancel(cancel)
        if model not in model_ids:
            raise GeminiError(f"{provider_label(self.provider)} không trả model {model} trong Models API.")
        return f"Kết nối {provider_label(self.provider)} thành công; model {model} hiện có trong Models API."
