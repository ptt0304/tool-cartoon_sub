from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

import httpx

from cartoon_sub.app.settings import LocalTTSSettings


class LocalTTSError(Exception):
    def __init__(self, code: str, message: str, status_code: int | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


class LocalTTSClient:
    def __init__(
        self,
        settings: LocalTTSSettings | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
    ):
        self.settings = (settings or LocalTTSSettings()).validate()
        timeout = httpx.Timeout(
            self.settings.timeout_seconds,
            connect=min(10, self.settings.timeout_seconds),
            write=min(30, self.settings.timeout_seconds),
            pool=min(10, self.settings.timeout_seconds),
        )
        self._client = httpx.Client(
            base_url=self.settings.base_url,
            timeout=timeout,
            transport=transport,
            follow_redirects=False,
        )

    def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        try:
            response = self._client.request(method, url, **kwargs)
        except httpx.TimeoutException as exc:
            raise LocalTTSError("TIMEOUT", "Local_TTS request timed out") from exc
        except httpx.RequestError as exc:
            raise LocalTTSError("CONNECTION_ERROR", "Could not connect to Local_TTS") from exc
        if response.is_success:
            return response
        code = "HTTP_ERROR"
        message = f"Local_TTS returned HTTP {response.status_code}"
        try:
            payload = response.json()
        except (ValueError, TypeError):
            payload = None
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                code = str(error.get("code") or code)
                message = str(error.get("message") or message)
            elif payload.get("detail"):
                detail = payload["detail"]
                message = detail if isinstance(detail, str) else str(detail)
        elif response.text.strip():
            message = response.text.strip()[:500]
        raise LocalTTSError(code, message, response.status_code)

    @staticmethod
    def _json_object(response: httpx.Response) -> dict:
        try:
            payload = response.json()
        except ValueError as exc:
            raise LocalTTSError("INVALID_RESPONSE", "Local_TTS returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise LocalTTSError("INVALID_RESPONSE", "Local_TTS returned an invalid response")
        return payload

    def health(self) -> dict:
        return self._json_object(self._request("GET", "/api/health"))

    def list_voices(self) -> list[dict]:
        payload = self._json_object(self._request("GET", "/api/voices"))
        voices = payload.get("voices")
        if not isinstance(voices, list) or any(not isinstance(item, dict) for item in voices):
            raise LocalTTSError("INVALID_RESPONSE", "Local_TTS voice list is invalid")
        return voices

    def list_ready_voices(self) -> list[dict]:
        return [voice for voice in self.list_voices() if voice.get("status") == "READY"]

    def get_voice(self, voice_id: str) -> dict:
        return self._json_object(self._request("GET", f"/api/voices/{quote(voice_id, safe='')}"))

    def preview_voice(self, voice_id: str) -> bytes:
        response = self._request("POST", f"/api/voices/{quote(voice_id, safe='')}/preview")
        return self._wav_bytes(response)

    def generate(
        self,
        segment_id: str,
        speaker_id: str,
        voice_id: str,
        text: str,
        speed: float,
    ) -> dict:
        payload = {
            "segment_id": segment_id,
            "speaker_id": speaker_id,
            "voice_id": voice_id,
            "text": text,
            "speed": speed,
            "pause_settings": None,
            "chunking_settings": None,
        }
        metadata = self._json_object(self._request("POST", "/api/tts/generate", json=payload))
        audio_url = metadata.get("audio_url")
        audio_path = metadata.get("audio_path")
        if not audio_url and not audio_path:
            raise LocalTTSError("INVALID_RESPONSE", "Local_TTS response has neither audio_url nor audio_path")
        return metadata

    def download_audio(self, audio_url: str | None = None, *, audio_path: str | None = None) -> bytes:
        if audio_url:
            url = httpx.URL(audio_url)
            if url.is_absolute_url:
                base = httpx.URL(self.settings.base_url)
                if (url.scheme, url.host, url.port) != (base.scheme, base.host, base.port):
                    raise LocalTTSError("INVALID_AUDIO_URL", "Local_TTS audio_url points to another server")
                request_url = str(url)
            elif audio_url.startswith("/"):
                request_url = audio_url
            else:
                raise LocalTTSError("INVALID_AUDIO_URL", "Local_TTS audio_url must be relative to the server")
            return self._wav_bytes(self._request("GET", request_url))
        if audio_path:
            p = Path(audio_path)
            if p.is_file():
                content = p.read_bytes()
                if not content or len(content) < 4 or content[:4] != b"RIFF":
                    raise LocalTTSError("INVALID_AUDIO", "Local_TTS did not return a valid WAV file")
                return content
            raise LocalTTSError("AUDIO_NOT_FOUND", f"Local_TTS audio file not found: {audio_path}")
        raise LocalTTSError("INVALID_RESPONSE", "Local_TTS response has no valid audio location")

    @staticmethod
    def _wav_bytes(response: httpx.Response) -> bytes:
        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type != "audio/wav" or not response.content:
            raise LocalTTSError("INVALID_AUDIO", "Local_TTS did not return a WAV file")
        return response.content

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()
