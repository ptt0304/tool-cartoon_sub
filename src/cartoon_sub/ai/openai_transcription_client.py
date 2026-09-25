"""OpenAI audio-transcription adapter; never logs credentials or response bodies."""
import io
import wave

import httpx

from cartoon_sub.ai.gemini_client import GeminiError
from cartoon_sub.project.cache import check_cancel


class OpenAITranscriptionClient:
    url = "https://api.openai.com/v1/audio/transcriptions"

    def __init__(self, api_key):
        self.api_key = api_key
        self.client = httpx.Client(timeout=120)

    def close(self):
        self.client.close()

    def _request(self, audio_bytes, model, response_format):
        try:
            response = self.client.post(
                self.url,
                headers={"Authorization": f"Bearer {self.api_key}"},
                files={"file": ("audio.wav", audio_bytes, "audio/wav")},
                data={"model": model, "language": "zh", "response_format": response_format,
                      "prompt": "Transcribe the Chinese speech accurately and preserve punctuation."},
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            raise GeminiError(f"OpenAI transcription HTTP {code}: kiểm tra API key, model, quota và audio.",
                              code == 429 or code >= 500) from None
        except (httpx.HTTPError, ValueError, TypeError):
            raise GeminiError("OpenAI transcription không trả dữ liệu hợp lệ.", True) from None

    @staticmethod
    def _duration(audio_bytes):
        with wave.open(io.BytesIO(audio_bytes), "rb") as audio:
            return audio.getnframes() / audio.getframerate()

    def transcribe_json(self, audio_bytes, prompt, schema, model, cancel=None, references=None, progress=None):
        del prompt, schema, references
        check_cancel(cancel)
        if progress:
            progress("[OPENAI] Transcribing audio…")
        try:
            payload = self._request(audio_bytes, model, "verbose_json")
        except GeminiError as exc:
            if "HTTP 400" not in str(exc):
                raise
            payload = self._request(audio_bytes, model, "json")
        check_cancel(cancel)
        duration = self._duration(audio_bytes)
        rows = payload.get("segments") if isinstance(payload, dict) else None
        if rows:
            segments = []
            for row in rows:
                text = row.get("text", "").strip()
                start = max(0.0, float(row.get("start", 0)))
                end = min(duration, float(row.get("end", duration)))
                if text and end > start:
                    segments.append({"start": start, "end": end, "zh": text,
                                     "speaker_id": "SPK_UNKNOWN"})
            return {"segments": segments}
        text = payload.get("text", "").strip() if isinstance(payload, dict) else ""
        return {"segments": ([{"start": 0.0, "end": duration, "zh": text,
                                "speaker_id": "SPK_UNKNOWN"}] if text and duration > 0 else [])}
