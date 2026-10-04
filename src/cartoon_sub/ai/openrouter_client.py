"""OpenRouter gateway, dynamic catalogs, and dedicated transcription adapter."""
import base64
import io
import json
import logging
import re
import threading
import time
import wave
from datetime import datetime, timezone
from pathlib import Path

import httpx

from cartoon_sub.ai.provider_errors import AIProviderError, ProviderErrorCategory
from cartoon_sub.media.process import CancelledError
from cartoon_sub.project.cache import atomic_json, check_cancel
from cartoon_sub.transcription.contracts import (
    TranscriptionCapability,
    normalize_confidence,
    normalize_speaker_hint,
    normalize_word_timestamps,
)


OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_MODELS_URL = f"{OPENROUTER_BASE_URL}/models"
OPENROUTER_TRANSCRIPTION_MODELS_URL = f"{OPENROUTER_MODELS_URL}?output_modalities=transcription"
OPENROUTER_CHAT_URL = f"{OPENROUTER_BASE_URL}/chat/completions"
OPENROUTER_TRANSCRIPTION_URL = f"{OPENROUTER_BASE_URL}/audio/transcriptions"
OPENROUTER_KEY_URL = f"{OPENROUTER_BASE_URL}/key"
log = logging.getLogger(__name__)


def parse_openrouter_api_keys(text):
    """Return unique non-empty keys in first-seen order; never log this value."""
    seen, keys = set(), []
    for line in str(text or "").splitlines():
        key = line.strip()
        if key and key not in seen:
            seen.add(key); keys.append(key)
    return keys


def mask_api_key(key):
    key = str(key or "")
    if len(key) <= 8:
        return "•" * max(4, len(key))
    return f"{key[:8]}...{key[-4:]}"


class OpenRouterKeyPool:
    """Thread-safe, ordered, session-only credential failover state."""
    def __init__(self, keys, *, cooldown_seconds=30, clock=None):
        self._keys = parse_openrouter_api_keys("\n".join(keys or []))
        self._auth_invalid = set()
        self._temporary_until = {}
        self._active_index = 0
        self._cooldown_seconds = max(0.0, float(cooldown_seconds))
        self._clock = clock or time.monotonic
        self._lock = threading.RLock()
        self._last_temporary_error = None

    @property
    def keys(self):
        with self._lock:
            return list(self._keys)

    def _expire_cooldowns(self):
        now = self._clock()
        for key, deadline in list(self._temporary_until.items()):
            if deadline <= now:
                del self._temporary_until[key]

    def available_keys(self):
        with self._lock:
            self._expire_cooldowns()
            return [key for key in self._keys
                    if key not in self._auth_invalid and key not in self._temporary_until]

    def _ordered_available_keys(self):
        with self._lock:
            self._expire_cooldowns()
            if not self._keys:
                return []
            ordered = self._keys[self._active_index:] + self._keys[:self._active_index]
            return [key for key in ordered
                    if key not in self._auth_invalid and key not in self._temporary_until]

    def current_key(self):
        available = self._ordered_available_keys()
        if not available:
            raise ValueError("OpenRouter API key is required.")
        return available[0]

    def mark_auth_invalid(self, key):
        with self._lock:
            if key in self._keys:
                self._auth_invalid.add(key)
                self._temporary_until.pop(key, None)

    def mark_temporary_failure(self, key, seconds=None):
        with self._lock:
            if key in self._keys:
                duration = self._cooldown_seconds if seconds is None else max(0.0, float(seconds))
                self._temporary_until[key] = self._clock() + duration

    def clear_temporary_failures(self):
        with self._lock:
            self._temporary_until.clear()

    def state(self, key):
        with self._lock:
            self._expire_cooldowns()
            if key in self._auth_invalid:
                return "AUTH_INVALID"
            if key in self._temporary_until:
                return "TEMPORARILY_UNAVAILABLE"
            return "AVAILABLE"

    def execute(self, operation, *, cancel=None, progress=None):
        """Run one logical request at most once per currently usable key."""
        candidates = self._ordered_available_keys()
        if not candidates:
            if self._last_temporary_error is not None and self._temporary_until:
                previous = self._last_temporary_error
                raise AIProviderError(
                    str(previous), previous.error_category,
                    status_code=previous.status_code,
                    retryable=previous.retryable,
                    retry_after_seconds=previous.retry_after_seconds,
                    provider_code=getattr(previous, "provider_code", None),
                    provider_message=getattr(previous, "provider_message", None),
                ) from None
            raise AIProviderError(
                "No usable OpenRouter API key is available.",
                ProviderErrorCategory.AUTH_INVALID,
            )
        last_error = None
        for position, key in enumerate(candidates):
            check_cancel(cancel)
            if self.state(key) != "AVAILABLE":
                continue
            try:
                result = operation(key)
            except CancelledError:
                raise
            except AIProviderError as exc:
                last_error = exc
                category = exc.error_category
                if category == ProviderErrorCategory.AUTH_INVALID:
                    self.mark_auth_invalid(key)
                elif category in {
                    ProviderErrorCategory.RATE_LIMITED,
                    ProviderErrorCategory.KEY_BUDGET_EXCEEDED,
                    ProviderErrorCategory.TIMEOUT,
                    ProviderErrorCategory.NETWORK_ERROR,
                    ProviderErrorCategory.SERVER_ERROR,
                }:
                    self._last_temporary_error = exc
                    self.mark_temporary_failure(key, exc.retry_after_seconds)
                else:
                    raise
                check_cancel(cancel)
                if position + 1 < len(candidates) and progress:
                    progress("OpenRouter request failed; trying another API key...")
                continue
            except Exception:
                raise AIProviderError(
                    "OpenRouter request failed.", ProviderErrorCategory.UNKNOWN
                ) from None
            with self._lock:
                self._active_index = self._keys.index(key)
            return result
        category = (last_error.error_category if last_error is not None
                    else ProviderErrorCategory.UNKNOWN)
        message = (str(last_error) if len(candidates) == 1 and last_error is not None
                   else "No usable OpenRouter API key is available.")
        raise AIProviderError(
            message,
            category,
            status_code=getattr(last_error, "status_code", None),
            retryable=False,
            retry_after_seconds=getattr(last_error, "retry_after_seconds", None),
            provider_code=getattr(last_error, "provider_code", None),
            provider_message=getattr(last_error, "provider_message", None),
        ) from None


def validate_openrouter_key(api_key, client=None):
    """Validate exactly one explicit key against OpenRouter's authenticated endpoint."""
    if not isinstance(api_key, str) or not api_key.strip():
        return {"status": "INVALID_OR_REVOKED", "message": "OpenRouter API key is required."}
    http = client or httpx.Client(timeout=15)
    owns_client = client is None
    try:
        response = http.get(OPENROUTER_KEY_URL,
                            headers={"Authorization": f"Bearer {api_key.strip()}"})
        if response.status_code == 200:
            return {"status": "VALID", "message": "Valid"}
        if response.status_code in (401, 403):
            return {"status": "INVALID_OR_REVOKED",
                    "message": "Invalid or revoked OpenRouter API key"}
        if response.status_code == 429:
            return {"status": "RATE_LIMITED", "message": "Rate limited"}
        if response.status_code == 408:
            return {"status": "TIMEOUT", "message": "OpenRouter request timed out"}
        if response.status_code >= 500:
            return {"status": "SERVER_ERROR",
                    "message": f"OpenRouter API error: HTTP {response.status_code}"}
        return {"status": "UNKNOWN_ERROR",
                "message": f"OpenRouter API error: HTTP {response.status_code}"}
    except httpx.TimeoutException:
        return {"status": "TIMEOUT", "message": "OpenRouter request timed out"}
    except httpx.HTTPError:
        return {"status": "NETWORK_ERROR", "message": "Cannot reach OpenRouter"}
    finally:
        if owns_client:
            http.close()


def _modalities(model, direction):
    architecture = model.get("architecture") if isinstance(model.get("architecture"), dict) else {}
    values = architecture.get(f"{direction}_modalities")
    if isinstance(values, list):
        return {str(value).casefold() for value in values}
    modality = architecture.get("modality")
    if isinstance(modality, str) and "->" in modality:
        side = modality.split("->", 1)[0 if direction == "input" else 1]
        return {part.strip().casefold() for part in side.replace("+", ",").split(",") if part.strip()}
    return set()


def parse_models(payload):
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise AIProviderError("OpenRouter không trả catalog model hợp lệ.",
                              ProviderErrorCategory.RESPONSE_ERROR, retryable=True)
    models, seen = [], set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        model_id = row.get("id")
        if not isinstance(model_id, str) or not model_id.strip() or model_id in seen:
            continue
        seen.add(model_id)
        models.append({
            "id": model_id,
            "name": row.get("name") if isinstance(row.get("name"), str) else model_id,
            "context_length": row.get("context_length"),
            "pricing": row.get("pricing") if isinstance(row.get("pricing"), dict) else {},
            "architecture": row.get("architecture") if isinstance(row.get("architecture"), dict) else {},
            "supported_parameters": list(row.get("supported_parameters", []))
                if isinstance(row.get("supported_parameters"), list) else [],
        })
    if not models:
        raise AIProviderError("OpenRouter không trả model nào trong catalog.",
                              ProviderErrorCategory.RESPONSE_ERROR, retryable=True)
    return models


def normalize_model_author(model_id):
    prefix = str(model_id or "").split("/", 1)[0].lstrip("~").strip()
    return prefix.casefold() or "other"


def model_author(model):
    model_id = model["id"] if isinstance(model, dict) else str(model)
    return normalize_model_author(model_id)


model_provider = model_author  # backward-compatible internal alias


def supports_capability(model, capability):
    inputs, outputs = _modalities(model, "input"), _modalities(model, "output")
    if not inputs or not outputs:
        return False
    if capability in ("text", "translation", "review"):
        return "text" in inputs and "text" in outputs
    if capability in ("vision", "vision_frames"):
        return {"text", "image"}.issubset(inputs) and "text" in outputs
    if capability == "vision_video":
        return {"text", "video"}.issubset(inputs) and "text" in outputs
    if capability == "transcription":
        return "transcription" in outputs
    return False


def filter_models(models, *, capability="text", author="all", provider=None, search=""):
    if provider is not None:
        author = provider
    needle = search.strip().casefold()
    result = []
    for model in models:
        if capability != "transcription_catalog" and not supports_capability(model, capability):
            continue
        current_author = model_author(model)
        if author != "all" and current_author != author:
            continue
        haystack = f"{model['id']} {model.get('name', '')} {current_author}".casefold()
        if needle and needle not in haystack:
            continue
        result.append(model)
    return result


def _sanitize_provider_text(value):
    """Keep bounded diagnostics while redacting credential-shaped values."""
    text = str(value or "").replace("\r", " ").replace("\n", " ")
    text = re.sub(r"(?i)bearer\s+[^\s,;]+", "Bearer [REDACTED]", text)
    text = re.sub(r"(?i)\b(sk|key)-[A-Za-z0-9_-]{8,}\b", "[REDACTED]", text)
    return text[:500]


def _response_error_details(response):
    """Extract sanitized OpenRouter semantics; never expose headers or credentials."""
    try:
        payload = response.json()
    except Exception:
        return {"code": None, "message": "", "type": None, "text": ""}
    if not isinstance(payload, dict):
        return {"code": None, "message": "", "type": None, "text": ""}
    error = payload.get("error", payload)
    if isinstance(error, dict):
        code, message, kind = error.get("code"), error.get("message"), error.get("type")
    else:
        code, message, kind = None, error, None
    clean_message = _sanitize_provider_text(message)
    text = " ".join(str(value) for value in (clean_message, code, kind)
                    if value is not None).casefold()
    return {"code": _sanitize_provider_text(code), "message": clean_message,
            "type": _sanitize_provider_text(kind), "text": text}


def _retry_after(response):
    try:
        value = float((response.headers or {}).get("retry-after"))
        return max(0.0, min(value, 300.0))
    except (TypeError, ValueError, AttributeError):
        return None


def _http_error(exc):
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        details = _response_error_details(exc.response)
        detail = details["text"]
        budget_error = any(marker in detail for marker in (
            "key budget", "key limit", "spend limit", "spending limit",
            "budget exceeded", "usage limit for this key",
        ))
        credit_error = any(marker in detail for marker in (
            "insufficient credit", "insufficient_credit", "not enough credit",
            "credit balance",
        ))
        payment_error = "payment required" in detail
        if code == 401:
            category, message = (ProviderErrorCategory.AUTH_INVALID,
                                 "Invalid or revoked OpenRouter API key.")
        elif code == 403:
            confirmed = any(marker in detail for marker in (
                "invalid api key", "api key is invalid", "revoked", "invalid credential",
                "invalid token", "authentication failed",
            ))
            if budget_error:
                category, message = (ProviderErrorCategory.KEY_BUDGET_EXCEEDED,
                                     "OpenRouter API key budget was exceeded.")
            elif credit_error:
                category, message = (ProviderErrorCategory.INSUFFICIENT_CREDITS,
                                     "OpenRouter account has insufficient credits.")
            elif payment_error:
                category, message = (ProviderErrorCategory.PAYMENT_REQUIRED,
                                     "OpenRouter requires payment for this request.")
            else:
                category = (ProviderErrorCategory.AUTH_INVALID if confirmed
                            else ProviderErrorCategory.MODEL_ERROR)
                message = ("Invalid or revoked OpenRouter API key." if confirmed else
                           "OpenRouter denied access to this request or model.")
        elif code in (400, 422):
            is_model_error = any(marker in detail for marker in (
                "model not found", "model_not_found", "unsupported model", "unknown model",
            ))
            category = (ProviderErrorCategory.MODEL_ERROR if is_model_error
                        else ProviderErrorCategory.BAD_REQUEST)
            message = ("OpenRouter model is unavailable." if is_model_error else
                       "OpenRouter rejected the request payload.")
        elif code == 404:
            category, message = (ProviderErrorCategory.MODEL_ERROR,
                                 "OpenRouter model is unavailable.")
        elif code == 402:
            category = (ProviderErrorCategory.KEY_BUDGET_EXCEEDED if budget_error
                        else ProviderErrorCategory.PAYMENT_REQUIRED if payment_error
                        else ProviderErrorCategory.INSUFFICIENT_CREDITS)
            message = ("OpenRouter API key budget was exceeded." if budget_error else
                       "OpenRouter requires payment for this request." if payment_error else
                       "OpenRouter account has insufficient credits.")
        elif code == 429:
            if budget_error:
                category, message = (ProviderErrorCategory.KEY_BUDGET_EXCEEDED,
                                     "OpenRouter API key budget was exceeded.")
            elif credit_error:
                category, message = (ProviderErrorCategory.INSUFFICIENT_CREDITS,
                                     "OpenRouter account has insufficient credits.")
            elif payment_error:
                category, message = (ProviderErrorCategory.PAYMENT_REQUIRED,
                                     "OpenRouter requires payment for this request.")
            else:
                category, message = (ProviderErrorCategory.RATE_LIMITED,
                                     "OpenRouter is rate limiting requests.")
        elif code == 408:
            category, message = (ProviderErrorCategory.TIMEOUT,
                                 "OpenRouter request timed out.")
        elif code in (500, 502, 503, 504):
            category, message = (ProviderErrorCategory.SERVER_ERROR,
                                 f"OpenRouter service error: HTTP {code}.")
        else:
            category, message = (ProviderErrorCategory.UNKNOWN,
                                 f"OpenRouter API error: HTTP {code}.")
        retryable = category in {
            ProviderErrorCategory.RATE_LIMITED, ProviderErrorCategory.TIMEOUT,
            ProviderErrorCategory.NETWORK_ERROR, ProviderErrorCategory.SERVER_ERROR,
        }
        error = AIProviderError(
            message, category, status_code=code, retryable=retryable,
            retry_after_seconds=_retry_after(exc.response),
            provider_code=details["code"], provider_message=details["message"],
        )
        log.warning(
            "[OPENROUTER ERROR] classification=%s http_status=%s provider_code=%r "
            "provider_message=%r retry_after=%r",
            category.value, code, details["code"], details["message"],
            error.retry_after_seconds,
        )
        return error
    if isinstance(exc, httpx.TimeoutException):
        return AIProviderError("OpenRouter request timed out.", ProviderErrorCategory.TIMEOUT)
    if isinstance(exc, httpx.RequestError):
        return AIProviderError("Cannot reach OpenRouter.", ProviderErrorCategory.NETWORK_ERROR)
    return AIProviderError("OpenRouter returned an invalid response.",
                           ProviderErrorCategory.RESPONSE_ERROR, retryable=True)


def _coerce_pool(credentials):
    if isinstance(credentials, OpenRouterKeyPool):
        return credentials
    if isinstance(credentials, str):
        keys = parse_openrouter_api_keys(credentials)
    else:
        keys = list(credentials or [])
    return OpenRouterKeyPool(keys)


def _openrouter_json_schema(value):
    """Convert legacy Gemini-style schema type names to standard JSON Schema."""
    if isinstance(value, list):
        return [_openrouter_json_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    output = {}
    for key, item in value.items():
        if key == "type" and isinstance(item, str):
            item = {
                "OBJECT": "object", "ARRAY": "array", "STRING": "string",
                "INTEGER": "integer", "NUMBER": "number", "BOOLEAN": "boolean",
            }.get(item, item)
        output[key] = _openrouter_json_schema(item)
    if output.get("type") == "object" and "properties" in output:
        output.setdefault("additionalProperties", False)
    return output


def _parse_json_object_content(content):
    """Parse provider content variants while returning only a JSON object."""
    if isinstance(content, dict):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                value = item.get("text", item.get("content", ""))
                if isinstance(value, str):
                    parts.append(value)
        content = "".join(parts)
    if not isinstance(content, str):
        raise ValueError
    text = content.strip()
    if text.startswith("```") and text.endswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        payload = json.loads(text)
        if isinstance(payload, str):
            payload = json.loads(payload)
    except json.JSONDecodeError as original_error:
        # Providers may prepend reasoning or append prose. Decode the first
        # complete JSON object instead of slicing from the first "{" to the
        # last "}", which breaks when reasoning itself contains braces.
        decoder = json.JSONDecoder()
        payload = None
        for start, char in enumerate(text):
            if char != "{":
                continue
            try:
                candidate, _end = decoder.raw_decode(text[start:])
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, dict):
                payload = candidate
                break
        if payload is None:
            raise original_error
    if not isinstance(payload, dict):
        raise ValueError
    return payload


class OpenRouterCatalog:
    def __init__(self, cache_path, credentials, client=None, *, transcription=False):
        self.cache_path = Path(cache_path)
        self.credentials = credentials
        self._pool = None
        self.client = client or httpx.Client(timeout=15)
        self._owns_client = client is None
        self.transcription = transcription

    @property
    def url(self):
        return OPENROUTER_TRANSCRIPTION_MODELS_URL if self.transcription else OPENROUTER_MODELS_URL

    def close(self):
        if self._owns_client:
            self.client.close()

    def load_cache(self):
        if not self.cache_path.is_file():
            return None
        try:
            payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
            models = parse_models({"data": payload.get("models")})
            return {"updated_at": payload.get("updated_at", ""), "models": models}
        except (OSError, ValueError, TypeError, AttributeError, AIProviderError):
            return None

    def key_pool(self):
        if self._pool is None:
            credentials = self.credentials() if callable(self.credentials) else self.credentials
            self._pool = _coerce_pool(credentials)
        return self._pool

    def fetch(self, *, cancel=None):
        check_cancel(cancel)
        def request(key):
            try:
                response = self.client.get(self.url, headers={"Authorization": f"Bearer {key}"})
                response.raise_for_status()
                return parse_models(response.json())
            except AIProviderError:
                raise
            except httpx.HTTPError as exc:
                raise _http_error(exc) from None
            except (ValueError, TypeError, KeyError, AttributeError):
                raise AIProviderError(
                    "OpenRouter did not return a valid model catalog.",
                    ProviderErrorCategory.RESPONSE_ERROR,
                    retryable=True,
                ) from None
        models = self.key_pool().execute(request, cancel=cancel)
        check_cancel(cancel)
        payload = {"updated_at": datetime.now(timezone.utc).isoformat(), "models": models}
        atomic_json(self.cache_path, payload)
        return payload

    def get_models(self, *, refresh=False, cancel=None):
        cached = self.load_cache()
        if cached is not None and not refresh:
            return cached
        return self.fetch(cancel=cancel)


class OpenRouterClient:
    def __init__(self, credentials, models=None, client=None):
        self.key_pool = _coerce_pool(credentials)
        self.models = {model["id"]: model for model in (models or [])}
        self.client = client or httpx.Client(timeout=120)
        self._owns_client = client is None
        self.last_http_status = None

    def close(self):
        if self._owns_client:
            self.client.close()

    def get_models(self, cancel=None):
        check_cancel(cancel)
        def request(key):
            try:
                response = self.client.get(OPENROUTER_MODELS_URL,
                    headers={"Authorization": f"Bearer {key}"})
                response.raise_for_status()
                return parse_models(response.json())
            except AIProviderError:
                raise
            except httpx.HTTPError as exc:
                raise _http_error(exc) from None
            except (ValueError, TypeError, KeyError, AttributeError):
                raise AIProviderError("OpenRouter did not return a valid model catalog.",
                    ProviderErrorCategory.RESPONSE_ERROR, retryable=True) from None
        models = self.key_pool.execute(request, cancel=cancel)
        check_cancel(cancel)
        return models

    def test_connection(self, model=None, cancel=None, progress=None):
        del model
        if progress:
            progress("Checking OpenRouter connection…")
        check_cancel(cancel)
        def request(key):
            try:
                response = self.client.get(OPENROUTER_KEY_URL,
                    headers={"Authorization": f"Bearer {key}"})
                response.raise_for_status()
                return True
            except httpx.HTTPError as exc:
                raise _http_error(exc) from None
        self.key_pool.execute(request, cancel=cancel, progress=progress)
        check_cancel(cancel)
        return "✓ Connected — API key valid"

    def _json_body(self, system, user_content, schema, model):
        if not isinstance(model, str) or not model.strip() or "/" not in model:
            raise AIProviderError("Select a valid OpenRouter model in Settings > AI.",
                                  ProviderErrorCategory.MODEL_ERROR)
        schema = _openrouter_json_schema(schema)
        contract = ("\nChỉ trả về một JSON object hợp lệ, không markdown. JSON phải tuân theo schema sau: "
                    + json.dumps(schema, ensure_ascii=False))
        if isinstance(user_content, str):
            user_content += contract
        else:
            user_content = list(user_content)
            user_content[0] = dict(user_content[0])
            user_content[0]["text"] = str(user_content[0].get("text", "")) + contract
        body = {"model": model, "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ]}
        supported = set(self.models.get(model, {}).get("supported_parameters", []))
        if "temperature" in supported:
            body["temperature"] = 0.3
        if "response_format" in supported or "structured_outputs" in supported:
            body["response_format"] = {"type": "json_schema", "json_schema": {
                "name": "cartoon_sub_response", "strict": True, "schema": schema,
            }}
        return body

    def _post_json(self, body, cancel=None, progress=None):
        def request(key):
            try:
                response = self.client.post(OPENROUTER_CHAT_URL,
                    headers={"Authorization": f"Bearer {key}"}, json=body)
                self.last_http_status = response.status_code
                response.raise_for_status()
                message = response.json()["choices"][0]["message"]
                candidates = [message.get("content")]
                if message.get("reasoning"):
                    candidates.append(message.get("reasoning"))
                reasoning_details = message.get("reasoning_details")
                if isinstance(reasoning_details, list):
                    candidates.extend(reasoning_details)
                for content in candidates:
                    try:
                        return _parse_json_object_content(content)
                    except (ValueError, TypeError, json.JSONDecodeError):
                        continue
                log.warning("[OPENROUTER JSON] response representation message_keys=%s "
                            "content_type=%s content_preview=%r",
                            sorted(message), type(message.get("content")).__name__,
                            str(message.get("content"))[:500])
                raise ValueError
            except httpx.HTTPError as exc:
                raise _http_error(exc) from None
            except (ValueError, TypeError, KeyError, IndexError, json.JSONDecodeError):
                raise AIProviderError("OpenRouter did not return valid JSON.",
                    ProviderErrorCategory.RESPONSE_ERROR, retryable=True) from None
        payload = self.key_pool.execute(request, cancel=cancel, progress=progress)
        check_cancel(cancel)
        return payload

    def generate_json(self, system, prompt, schema, model, cancel=None):
        check_cancel(cancel)
        return self._post_json(self._json_body(system, prompt, schema, model), cancel=cancel)

    def generate_multimodal_json(self, system, prompt, media, schema, model,
                                 cancel=None, progress=None):
        """Send bounded image/video evidence through the shared rotating key pool."""
        check_cancel(cancel)
        content = [{"type": "text", "text": prompt}]
        for mime_type, payload in media:
            encoded = base64.b64encode(payload).decode("ascii")
            if mime_type.startswith("image/"):
                content.append({"type": "image_url", "image_url": {
                    "url": f"data:{mime_type};base64,{encoded}",
                }})
            elif mime_type.startswith("video/"):
                content.append({"type": "video_url", "video_url": {
                    "url": f"data:{mime_type};base64,{encoded}",
                }})
            else:
                raise ValueError(f"Unsupported visual media type: {mime_type}")
        body = self._json_body(system, content, schema, model)
        return self._post_json(body, cancel=cancel, progress=progress)


class OpenRouterTranscriptionClient:
    """Adapter for OpenRouter's dedicated /audio/transcriptions endpoint."""
    def __init__(self, credentials, client=None):
        self.key_pool = _coerce_pool(credentials)
        self.client = client or httpx.Client(timeout=120)
        self._owns_client = client is None

    def close(self):
        if self._owns_client:
            self.client.close()

    @staticmethod
    def _duration(audio_bytes):
        with wave.open(io.BytesIO(audio_bytes), "rb") as audio:
            return audio.getnframes() / audio.getframerate()

    def transcribe_json(self, audio_bytes, prompt, schema, model, cancel=None,
                        references=None, progress=None):
        del prompt, schema, references
        check_cancel(cancel)
        if progress:
            progress("[OPENROUTER] Transcribing audio…")
        body = {
            "model": model,
            "input_audio": {"data": base64.b64encode(audio_bytes).decode("ascii"), "format": "wav"},
            "language": "zh",
            "response_format": "verbose_json",
        }
        def request(key):
            try:
                response = self.client.post(OPENROUTER_TRANSCRIPTION_URL,
                    headers={"Authorization": f"Bearer {key}"}, json=body)
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise ValueError
                return payload
            except httpx.HTTPError as exc:
                raise _http_error(exc) from None
            except (ValueError, TypeError, KeyError, AttributeError):
                raise AIProviderError("OpenRouter transcription returned an invalid response.",
                    ProviderErrorCategory.RESPONSE_ERROR, retryable=True) from None
        payload = self.key_pool.execute(request, cancel=cancel, progress=progress)
        check_cancel(cancel)
        try:
            duration = self._duration(audio_bytes)
            rows = payload.get("segments") if isinstance(payload, dict) else None
            root_words = normalize_word_timestamps(payload.get("words"), duration)
            words = list(root_words)
            actual = {TranscriptionCapability.TRANSCRIPTION.value}
            if rows:
                actual.add(TranscriptionCapability.SEGMENT_TIMESTAMPS.value)
                segments = []
                for row in rows:
                    text = str(row.get("text", "")).strip()
                    start = max(0.0, float(row.get("start", 0)))
                    end = min(duration, float(row.get("end", duration)))
                    speaker_id = normalize_speaker_hint(
                        row.get("speaker_id", row.get("speaker", "SPK_UNKNOWN")))
                    speaker_confidence = normalize_confidence(row.get("speaker_confidence"))
                    transcript_confidence = normalize_confidence(
                        row.get("transcript_confidence", row.get("confidence")))
                    if not root_words:
                        words.extend(normalize_word_timestamps(row.get("words"), duration))
                    if text and end > start:
                        segment = {"start": start, "end": end, "zh": text,
                                   "speaker_id": speaker_id}
                        if speaker_confidence is not None:
                            segment["speaker_confidence"] = speaker_confidence
                        if transcript_confidence is not None:
                            segment["transcript_confidence"] = transcript_confidence
                        segments.append(segment)
                if any(row["speaker_id"] != "SPK_UNKNOWN" for row in segments):
                    actual.add(TranscriptionCapability.SPEAKER_HINTS.value)
                result = {"segments": segments}
            else:
                text = payload.get("text", "").strip() if isinstance(payload, dict) else ""
                result = {"segments": ([{"start": 0.0, "end": duration, "zh": text,
                                         "speaker_id": "SPK_UNKNOWN"}]
                                       if text and duration > 0 else [])}
            if words:
                actual.add(TranscriptionCapability.WORD_TIMESTAMPS.value)
                if any(word.get("speaker_id") for word in words):
                    actual.add(TranscriptionCapability.SPEAKER_HINTS.value)
                result["words"] = words
            language = payload.get("language") if isinstance(payload, dict) else None
            if isinstance(language, str) and language.strip():
                result["language"] = language.strip()
            result["transcription_capabilities"] = sorted(actual)
            result["transcription_provider"] = "openrouter_dedicated"
            return result
        except (ValueError, TypeError, KeyError, AttributeError, wave.Error):
            raise AIProviderError("OpenRouter transcription returned an invalid response.",
                ProviderErrorCategory.RESPONSE_ERROR, retryable=True) from None


def openrouter_client_factory(models=None):
    return lambda credentials: OpenRouterClient(credentials, models=models)
