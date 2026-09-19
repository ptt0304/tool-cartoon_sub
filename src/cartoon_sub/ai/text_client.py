"""Text-only JSON clients for translation/context providers."""
import json
import httpx
from cartoon_sub.ai.gemini_client import GeminiError
from cartoon_sub.project.cache import check_cancel


PROVIDERS = {
    "openai": ("OpenAI", "https://api.openai.com/v1/chat/completions", "gpt-4o-mini"),
    "anthropic": ("Anthropic Claude", "https://api.anthropic.com/v1/messages", "claude-3-5-haiku-latest"),
    "openrouter": ("OpenRouter", "https://openrouter.ai/api/v1/chat/completions", "google/gemini-2.0-flash-001"),
    "deepseek": ("DeepSeek", "https://api.deepseek.com/chat/completions", "deepseek-chat"),
    "groq": ("Groq", "https://api.groq.com/openai/v1/chat/completions", "llama-3.3-70b-versatile"),
    "mistral": ("Mistral AI", "https://api.mistral.ai/v1/chat/completions", "mistral-small-latest"),
    "together": ("Together AI", "https://api.together.xyz/v1/chat/completions", "meta-llama/Llama-3.3-70B-Instruct-Turbo"),
    "fireworks": ("Fireworks AI", "https://api.fireworks.ai/inference/v1/chat/completions", "accounts/fireworks/models/llama-v3p3-70b-instruct"),
    "xai": ("xAI Grok", "https://api.x.ai/v1/chat/completions", "grok-3-mini"),
}

MODEL_PRESETS = {
    "gemini": ("gemini-3.5-flash", "gemini-3.1-flash-lite", "gemini-2.5-flash"),
    "openai": ("gpt-4o-mini", "gpt-4o", "gpt-4.1-mini"),
    "anthropic": ("claude-3-5-haiku-latest", "claude-3-7-sonnet-latest", "claude-sonnet-4-0"),
    "openrouter": ("google/gemini-2.0-flash-001", "anthropic/claude-3.7-sonnet", "openai/gpt-4o-mini"),
    "deepseek": ("deepseek-chat", "deepseek-reasoner"),
    "groq": ("llama-3.3-70b-versatile", "llama-3.1-8b-instant", "qwen-qwq-32b"),
    "mistral": ("mistral-small-latest", "mistral-large-latest", "ministral-8b-latest"),
    "together": ("meta-llama/Llama-3.3-70B-Instruct-Turbo", "Qwen/Qwen2.5-72B-Instruct-Turbo", "deepseek-ai/DeepSeek-R1"),
    "fireworks": ("accounts/fireworks/models/llama-v3p3-70b-instruct", "accounts/fireworks/models/qwen2p5-72b-instruct", "accounts/fireworks/models/deepseek-r1"),
    "xai": ("grok-3-mini", "grok-3", "grok-2-latest"),
}


def provider_label(provider):
    return "Google Gemini" if provider == "gemini" else PROVIDERS[provider][0]


def provider_models(provider):
    return MODEL_PRESETS[provider]


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


def text_client_factory(provider):
    return lambda key: TextProviderClient(provider, key)
