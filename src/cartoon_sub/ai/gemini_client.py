"""Only this adapter talks to Google; no credential or response-body logging."""
from pathlib import Path
from cartoon_sub.project.cache import check_cancel


KEY_POOL_ROUNDS = 3


def load_gemini_keys(path):
    path = Path(path)
    if not path.is_file():
        raise ValueError("Không tìm thấy file API key.")
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError):
        raise ValueError("Không đọc được file API key.") from None
    keys = list(dict.fromkeys(line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")))
    if not keys:
        raise ValueError("File không chứa Gemini API key hợp lệ.")
    return keys


class GeminiError(RuntimeError):
    def __init__(self, message, retryable=False, retry_after_seconds=None, quota_exhausted=False,
                 rotate_key=False, status_code=None, category="UNKNOWN"):
        super().__init__(message)
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds
        self.quota_exhausted = quota_exhausted
        self.rotate_key = rotate_key
        self.status_code = status_code
        self.category = category


def safe_error(exc):
    code = getattr(exc, "code", None)
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", {}) or {}
    retry_after = headers.get("retry-after")
    try:
        retry_after = max(0.0, float(retry_after)) if retry_after is not None else None
    except (TypeError, ValueError):
        retry_after = None
    if code == 400:
        return GeminiError("Gemini HTTP 400: kiểm tra model và định dạng request/audio.",
                           status_code=400, category="BAD_REQUEST")
    if code in (401, 403):
        return GeminiError(f"Gemini HTTP {code}: key không hợp lệ hoặc không có quyền API.", rotate_key=True,
                           status_code=code, category="AUTH" if code == 401 else "PERMISSION")
    if code == 404:
        return GeminiError("Gemini HTTP 404: model không có sẵn cho request này. Mở Settings > AI, "
                           "chọn một model hiện hành rồi Save và thử lại; đồng thời kiểm tra quyền truy cập.",
                           status_code=404, category="MODEL_NOT_FOUND")
    if code == 408:
        # Transcript classifies this structured status itself. Shared features
        # retain their previous no-retry behavior in this task.
        return GeminiError("Gemini HTTP 408: request hết thời gian chờ.", retry_after_seconds=retry_after,
                           status_code=408, category="TRANSIENT")
    if code == 422:
        return GeminiError("Gemini HTTP 422: request không hợp lệ.",
                           status_code=422, category="BAD_REQUEST")
    if code == 429:
        detail = str(exc).lower()
        exhausted = "daily quota" in detail or "quota exhausted" in detail
        message = ("GEMINI_QUOTA_EXHAUSTED: Gemini HTTP 429 báo quota ngày đã hết."
                   if exhausted else "Gemini HTTP 429: hết quota hoặc vượt giới hạn tốc độ. Kiểm tra quota trong AI Studio.")
        return GeminiError(message, not exhausted, retry_after, exhausted, rotate_key=True,
                           status_code=429, category="TRANSIENT")
    if isinstance(code, int) and 500 <= code < 600:
        return GeminiError(f"Gemini HTTP {code}: dịch vụ tạm thời gặp lỗi.", True, retry_after,
                           rotate_key=True, status_code=code,
                           category="TRANSIENT" if code in (500, 502, 503, 504) else "SERVER_ERROR")
    # Never surface raw SDK exceptions: they may contain request bodies or credentials.
    import httpx
    if isinstance(exc, (httpx.TransportError, TimeoutError, ConnectionError)):
        return GeminiError("Không kết nối được Gemini hoặc request hết thời gian chờ. Kiểm tra mạng/proxy.",
                           True, rotate_key=True, category="TRANSIENT")
    return GeminiError("Gemini không xử lý được request. Kiểm tra model, cấu hình và phiên bản google-genai.")


class GeminiClient:
    managed_transcript_retry = True

    def __init__(self, api_key):
        self.key_pool_enabled = not isinstance(api_key, str)
        self._api_keys = [api_key] if isinstance(api_key, str) else list(api_key)
        if not self._api_keys:
            raise ValueError("Không có Gemini API key để sử dụng.")
        self._key_index = 0
        self.client = self._create_client(self._api_keys[0])

    @staticmethod
    def _create_client(api_key):
        from google import genai
        from google.genai import types
        try:
            return genai.Client(api_key=api_key, vertexai=False, http_options=types.HttpOptions(
                timeout=120_000, retry_options=types.HttpRetryOptions(attempts=1)))
        except Exception as exc:
            raise safe_error(exc) from None

    def _select_key(self, index):
        if index == self._key_index:
            return
        self.client.close()
        self.client = self._create_client(self._api_keys[index])
        self._key_index = index

    @property
    def transcript_key_count(self):
        return len(self._api_keys)

    def select_transcript_key(self, index):
        """Select one key for Transcript's own bounded retry policy."""
        self._select_key(index)

    def _request_once(self, operation, cancel=None):
        """One sanitized request; intentionally does not rotate keys or retry."""
        check_cancel(cancel)
        try:
            return operation()
        except Exception as exc:
            raise (exc if isinstance(exc, GeminiError) else safe_error(exc)) from None

    def _request(self, operation, cancel=None, progress=None):
        if not getattr(self, "key_pool_enabled", False):
            try:
                return operation()
            except Exception as exc:
                raise (exc if isinstance(exc, GeminiError) else safe_error(exc)) from None
        total = len(self._api_keys)
        for round_index in range(KEY_POOL_ROUNDS):
            for key_index in range(total):
                check_cancel(cancel)
                if progress:
                    progress(f"[GEMINI] Vòng {round_index + 1}/{KEY_POOL_ROUNDS} - key {key_index + 1}/{total}")
                try:
                    self._select_key(key_index)
                    result = operation()
                    self._key_index = key_index
                    if progress:
                        progress(f"[GEMINI] Key {key_index + 1}/{total} thành công")
                    return result
                except Exception as exc:
                    error = exc if isinstance(exc, GeminiError) else safe_error(exc)
                    if not (error.rotate_key or error.retryable or error.quota_exhausted):
                        raise error from None
                    if progress:
                        progress(f"[GEMINI] Key {key_index + 1}/{total} lỗi; chuyển key tiếp theo")
        raise GeminiError(f"Gemini thất bại sau 3 vòng thử toàn bộ {total} API keys.")

    def close(self):
        self.client.close()

    def test_connection(self, model, cancel=None, progress=None):
        check_cancel(cancel)
        if progress:
            progress("Đang kiểm tra key và thông tin model…")
        info = self._request(lambda: self.client.models.get(model=model), cancel, progress)
        check_cancel(cancel)
        actions = getattr(info, "supported_actions", None)
        if actions and "generateContent" not in actions:
            raise GeminiError("Model này không hỗ trợ generateContent. Chọn model nhận audio và trả structured JSON.")
        message = ("Đọc metadata model thành công. Chưa kiểm tra quyền generateContent, quota inference "
                   "hay chất lượng transcription.")
        if self.key_pool_enabled:
            message += f" PASS using key {self._key_index + 1}/{len(self._api_keys)}."
        return message

    def _transcribe_json(self, audio_bytes, prompt, schema, model, cancel, references, progress, request):
        from google.genai import types
        check_cancel(cancel)
        contents=[prompt]
        for speaker_id, audio in (references or {}).items():
            contents.extend([f"VOICE REFERENCE ONLY: {speaker_id}", types.Part.from_bytes(data=audio,mime_type="audio/wav")])
        # Keep the old two-part request when no reference is present.
        if references: contents.append("TARGET AUDIO TO TRANSCRIBE:")
        contents.append(types.Part.from_bytes(data=audio_bytes,mime_type="audio/wav"))
        for token_limit in (16384, 32768):
            check_cancel(cancel)
            response = request(lambda: self.client.models.generate_content(
                    model=model, contents=contents,
                    config=types.GenerateContentConfig(response_mime_type="application/json",
                        response_schema=schema, temperature=0, max_output_tokens=token_limit),
                ))
            check_cancel(cancel)
            candidates = response.candidates or []
            if not candidates:
                feedback = getattr(response, 'prompt_feedback', None)
                blocked = getattr(feedback, 'block_reason', None)
                code = getattr(blocked, 'value', blocked)
                # Only expose known enum values, never response text or server messages.
                code = code if code in ('SAFETY', 'BLOCKLIST', 'PROHIBITED_CONTENT', 'OTHER') else 'NO_CANDIDATES'
                raise GeminiError(f"Gemini không trả transcript ({code}). Không phải bằng chứng key sai. Transcript cũ được giữ nguyên.")
            reason = getattr(candidates[0].finish_reason, 'value', candidates[0].finish_reason)
            if reason == 'STOP':
                break
            if reason == 'MAX_TOKENS':
                if token_limit == 16384:
                    continue
                raise GeminiError("Gemini MAX_TOKENS: transcript bị cắt vì hết giới hạn đầu ra, kể cả sau khi thử lại với 32768 token. Transcript cũ được giữ nguyên.")
            code = reason if reason in ('SAFETY', 'RECITATION', 'BLOCKLIST', 'PROHIBITED_CONTENT', 'SPII', 'OTHER', 'MALFORMED_FUNCTION_CALL') else 'UNKNOWN_FINISH_REASON'
            raise GeminiError(f"Gemini dừng transcription ({code}); không lưu kết quả dở dang. Transcript cũ được giữ nguyên.")
        if not response.text:
            raise GeminiError("Gemini không trả nội dung JSON.")
        return response.text

    def transcribe_json(self, audio_bytes, prompt, schema, model, cancel=None, references=None, progress=None):
        return self._transcribe_json(audio_bytes, prompt, schema, model, cancel, references, progress,
            lambda operation: self._request(operation, cancel, progress))

    def transcribe_json_once(self, audio_bytes, prompt, schema, model, cancel=None, references=None, progress=None):
        """Transcript-only primitive; GeminiTranscriber owns retry/backoff/key rotation."""
        return self._transcribe_json(audio_bytes, prompt, schema, model, cancel, references, progress,
            lambda operation: self._request_once(operation, cancel))

    def generate_json(self, system, prompt, schema, model, cancel=None):
        """Text-only requests for translation and its context analysis; no media input."""
        from google.genai import types
        check_cancel(cancel)
        response = self._request(lambda: self.client.models.generate_content(model=model, contents=prompt,
                config=types.GenerateContentConfig(system_instruction=system, response_mime_type="application/json",
                    response_schema=schema, temperature=0.3, max_output_tokens=16384)), cancel)
        check_cancel(cancel)
        candidates = response.candidates or []
        if not candidates:
            raise GeminiError("Gemini không trả kết quả. Bản dịch hiện tại được giữ lại.")
        reason = getattr(candidates[0].finish_reason, "value", candidates[0].finish_reason)
        if reason != "STOP":
            raise GeminiError("Gemini trả kết quả chưa hoàn tất hoặc bị chặn. Thử giảm chunk size trong Settings.",
                              retryable=reason == "MAX_TOKENS")
        if not response.text:
            raise GeminiError("Gemini không trả JSON cho bản dịch.")
        return response.text

    def generate_video_json(self, system, prompt, video_bytes, mime_type, schema, model,
                            cancel=None, progress=None):
        """Structured multimodal request used only by cached visual-context analysis."""
        from google.genai import types
        check_cancel(cancel)
        if not isinstance(video_bytes, bytes) or not video_bytes:
            raise ValueError("Video context proxy rỗng")
        contents = [prompt, types.Part.from_bytes(data=video_bytes, mime_type=mime_type)]
        response = self._request(lambda: self.client.models.generate_content(
            model=model,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system,
                response_mime_type="application/json",
                response_schema=schema,
                temperature=0.1,
                max_output_tokens=16384,
            ),
        ), cancel, progress)
        check_cancel(cancel)
        candidates = response.candidates or []
        if not candidates:
            raise GeminiError("Gemini không trả visual context.")
        reason = getattr(candidates[0].finish_reason, "value", candidates[0].finish_reason)
        if reason != "STOP":
            raise GeminiError("Gemini visual context chưa hoàn tất hoặc bị chặn.", retryable=reason == "MAX_TOKENS")
        if not response.text:
            raise GeminiError("Gemini không trả JSON visual context.")
        return response.text
