"""Only this adapter talks to Google; no credential or response-body logging."""
from cartoon_sub.project.cache import check_cancel


class GeminiError(RuntimeError):
    def __init__(self, message, retryable=False, retry_after_seconds=None, quota_exhausted=False):
        super().__init__(message)
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds
        self.quota_exhausted = quota_exhausted


def safe_error(exc):
    code = getattr(exc, "code", None)
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", {}) or {}
    retry_after = headers.get("retry-after")
    try:
        retry_after = max(0.0, float(retry_after)) if retry_after is not None else None
    except (TypeError, ValueError):
        retry_after = None
    if code in (400, 401, 403):
        return GeminiError(f"Gemini HTTP {code}: kiểm tra key, quyền API và model/định dạng audio.")
    if code == 404:
        return GeminiError("Gemini HTTP 404: model không có sẵn cho request này. Nếu đang dùng gemini-2.5-flash, "
                           "Google có thể hạn chế model này với project mới. Mở Settings > AI, chọn "
                           "gemini-3.5-flash rồi Save và thử lại. Không cần tạo lại project video. "
                           "Nếu dùng model khác, kiểm tra tên model và quyền truy cập.")
    if code == 429:
        detail = str(exc).lower()
        exhausted = "daily quota" in detail or "quota exhausted" in detail
        message = ("GEMINI_QUOTA_EXHAUSTED: Gemini HTTP 429 báo quota ngày đã hết."
                   if exhausted else "Gemini HTTP 429: hết quota hoặc vượt giới hạn tốc độ. Kiểm tra quota trong AI Studio.")
        return GeminiError(message, not exhausted, retry_after, exhausted)
    if isinstance(code, int) and 500 <= code < 600:
        return GeminiError(f"Gemini HTTP {code}: dịch vụ tạm thời gặp lỗi.", True, retry_after)
    # Never surface raw SDK exceptions: they may contain request bodies or credentials.
    import httpx
    if isinstance(exc, (httpx.TransportError, TimeoutError, ConnectionError)):
        return GeminiError("Không kết nối được Gemini hoặc request hết thời gian chờ. Kiểm tra mạng/proxy.", True)
    return GeminiError("Gemini không xử lý được request. Kiểm tra model, cấu hình và phiên bản google-genai.")


class GeminiClient:
    def __init__(self, api_key):
        from google import genai
        from google.genai import types
        try:
            self.client = genai.Client(api_key=api_key, vertexai=False, http_options=types.HttpOptions(
                timeout=120_000, retry_options=types.HttpRetryOptions(attempts=1)))
        except Exception as exc:
            raise safe_error(exc) from None

    def close(self):
        self.client.close()

    def test_connection(self, model, cancel=None, progress=None):
        check_cancel(cancel)
        if progress:
            progress("Đang kiểm tra key và thông tin model…")
        try:
            info = self.client.models.get(model=model)
        except Exception as exc:
            raise safe_error(exc) from None
        check_cancel(cancel)
        actions = getattr(info, "supported_actions", None)
        if actions and "generateContent" not in actions:
            raise GeminiError("Model này không hỗ trợ generateContent. Chọn model nhận audio và trả structured JSON.")
        message = ("Đọc metadata model thành công. Chưa kiểm tra quyền generateContent, quota inference "
                   "hay chất lượng transcription.")
        if model.removeprefix("models/").startswith("gemini-2.5-"):
            message += " Model 2.5 có thể trả 404 với project mới; nên chọn gemini-3.5-flash rồi Save."
        return message

    def transcribe_json(self, audio_bytes, prompt, schema, model, cancel=None, references=None):
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
            try:
                response = self.client.models.generate_content(
                    model=model, contents=contents,
                    config=types.GenerateContentConfig(response_mime_type="application/json",
                        response_schema=schema, temperature=0, max_output_tokens=token_limit),
                )
            except Exception as exc:
                raise safe_error(exc) from None
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

    def generate_json(self, system, prompt, schema, model, cancel=None):
        """Text-only requests for translation and its context analysis; no media input."""
        from google.genai import types
        check_cancel(cancel)
        try:
            response = self.client.models.generate_content(model=model, contents=prompt,
                config=types.GenerateContentConfig(system_instruction=system, response_mime_type="application/json",
                    response_schema=schema, temperature=0.3, max_output_tokens=16384))
        except Exception as exc:
            raise safe_error(exc) from None
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
