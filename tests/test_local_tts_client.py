import json
import tempfile
import unittest
from pathlib import Path

import httpx

from cartoon_sub.app.settings import LocalTTSSettings, SettingsStore
from cartoon_sub.tts.local_tts_client import LocalTTSClient, LocalTTSError


WAV_BYTES = b"RIFFmock-generated-wav"


class LocalTTSClientTests(unittest.TestCase):
    def setUp(self):
        self.requests = []

        def handler(request):
            self.requests.append(request)
            path = request.url.path
            if path == "/api/health":
                return httpx.Response(200, json={"status": "READY", "voices": 1})
            if path == "/api/voices":
                return httpx.Response(200, json={"voices": [
                    {"voice_id": "ready", "display_name": "Ready", "status": "READY", "engine": "vieneu"},
                    {"voice_id": "pending", "display_name": "Pending", "status": "REQUIRES_REFERENCE", "engine": "vieneu"},
                ]})
            if path == "/api/voices/ready":
                return httpx.Response(200, json={"voice_id": "ready", "status": "READY"})
            if path == "/api/voices/missing":
                return httpx.Response(404, json={"error": {"code": "VOICE_NOT_FOUND", "message": "voice_id is unknown"}})
            if path == "/api/voices/ready/preview":
                return httpx.Response(200, content=WAV_BYTES, headers={"content-type": "audio/wav"})
            if path == "/api/tts/generate":
                body = json.loads(request.content)
                return httpx.Response(200, json={
                    "segment_id": body["segment_id"],
                    "speaker_id": body["speaker_id"],
                    "voice_id": body["voice_id"],
                    "duration": 1.25,
                    "sample_rate": 48000,
                    "generation_time": 0.5,
                    "audio_path": "D:/remote/output.wav",
                    "audio_url": f"/api/tts/audio/{body['segment_id']}",
                })
            if path.startswith("/api/tts/audio/"):
                return httpx.Response(200, content=WAV_BYTES, headers={"content-type": "audio/wav"})
            return httpx.Response(500)

        self.client = LocalTTSClient(
            LocalTTSSettings("https://tts.example.test/", 120),
            transport=httpx.MockTransport(handler),
        )

    def tearDown(self):
        self.client.close()

    def test_health_voice_listing_and_ready_filtering(self):
        self.assertEqual(self.client.health(), {"status": "READY", "voices": 1})
        self.assertEqual(len(self.client.list_voices()), 2)
        self.assertEqual([voice["voice_id"] for voice in self.client.list_ready_voices()], ["ready"])
        self.assertEqual(self.client.get_voice("ready")["status"], "READY")

    def test_preview_generate_payload_audio_url_and_download(self):
        self.assertEqual(self.client.preview_voice("ready"), WAV_BYTES)
        metadata = self.client.generate("utt_000031_abc", "SPK_01", "ready", "Lời lồng tiếng", 0.95)
        generated_request = next(request for request in self.requests if request.url.path == "/api/tts/generate")
        self.assertEqual(json.loads(generated_request.content), {
            "segment_id": "utt_000031_abc",
            "speaker_id": "SPK_01",
            "voice_id": "ready",
            "text": "Lời lồng tiếng",
            "speed": 0.95,
            "pause_settings": None,
            "chunking_settings": None,
        })
        self.assertEqual(metadata["audio_url"], "/api/tts/audio/utt_000031_abc")
        self.assertEqual(self.client.download_audio(metadata["audio_url"]), WAV_BYTES)

    def test_structured_error_is_preserved(self):
        with self.assertRaises(LocalTTSError) as caught:
            self.client.get_voice("missing")
        self.assertEqual(caught.exception.code, "VOICE_NOT_FOUND")
        self.assertEqual(caught.exception.status_code, 404)
        self.assertIn("unknown", caught.exception.message)

    def test_connection_error(self):
        def fail(request):
            raise httpx.ConnectError("offline", request=request)

        client = LocalTTSClient(transport=httpx.MockTransport(fail))
        try:
            with self.assertRaises(LocalTTSError) as caught:
                client.health()
            self.assertEqual(caught.exception.code, "CONNECTION_ERROR")
        finally:
            client.close()

    def test_timeout(self):
        def fail(request):
            raise httpx.ReadTimeout("slow", request=request)

        client = LocalTTSClient(transport=httpx.MockTransport(fail))
        try:
            with self.assertRaises(LocalTTSError) as caught:
                client.health()
            self.assertEqual(caught.exception.code, "TIMEOUT")
        finally:
            client.close()

    def test_rejects_cross_origin_audio_url(self):
        with self.assertRaises(LocalTTSError) as caught:
            self.client.download_audio("https://other.example/api/tts/audio/segment")
        self.assertEqual(caught.exception.code, "INVALID_AUDIO_URL")

    def test_error_responses_never_leak_parsing_exceptions(self):
        cases = [
            (httpx.Response(500, json={"detail": "backend failed"}), "backend failed"),
            (httpx.Response(500, json=[]), "[]"),
            (httpx.Response(500, text="Internal Server Error"), "Internal Server Error"),
            (httpx.Response(500, content=b""), "Local_TTS returned HTTP 500"),
        ]
        for response, expected_message in cases:
            with self.subTest(expected_message=expected_message):
                client = LocalTTSClient(transport=httpx.MockTransport(lambda request, value=response: value))
                try:
                    with self.assertRaises(LocalTTSError) as caught:
                        client.health()
                    self.assertEqual(caught.exception.code, "HTTP_ERROR")
                    self.assertEqual(caught.exception.status_code, 500)
                    self.assertEqual(caught.exception.message, expected_message)
                finally:
                    client.close()

    def test_cross_origin_redirect_is_not_followed(self):
        requests = []

        def redirect(request):
            requests.append(str(request.url))
            if request.url.host == "127.0.0.1":
                return httpx.Response(302, headers={"location": "https://external.example/api/health"})
            return httpx.Response(200, json={"status": "READY"})

        client = LocalTTSClient(transport=httpx.MockTransport(redirect))
        try:
            with self.assertRaises(LocalTTSError) as caught:
                client.health()
            self.assertEqual(caught.exception.code, "HTTP_ERROR")
            self.assertEqual(caught.exception.status_code, 302)
            self.assertEqual(len(requests), 1)
        finally:
            client.close()

    def test_global_settings_roundtrip_and_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SettingsStore(folder=directory)
            settings = LocalTTSSettings("https://tunnel.example/", 600)
            store.save_local_tts(settings)
            self.assertEqual(Path(directory, "local_tts.json").is_file(), True)
            loaded = store.load_local_tts()
            self.assertEqual(loaded.base_url, "https://tunnel.example")
            self.assertEqual(loaded.timeout_seconds, 600)
        normalized = LocalTTSSettings("  HTTPS://Example.COM:443/  ", 300).validate()
        self.assertEqual(normalized.base_url, "https://example.com")
        for url in ("", "ftp://example.test", "http://"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                LocalTTSSettings(url, 300).validate()
        for timeout in (9, 1801, 10.0):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                LocalTTSSettings(timeout_seconds=timeout).validate()


    def test_generate_audio_path_fallback(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            wav_file = Path(tmpdir) / "output.wav"
            wav_file.write_bytes(WAV_BYTES)

            def handler(request):
                if request.url.path == "/api/tts/generate":
                    body = json.loads(request.content)
                    return httpx.Response(200, json={
                        "segment_id": body["segment_id"],
                        "speaker_id": body["speaker_id"],
                        "voice_id": body["voice_id"],
                        "duration": 1.25,
                        "sample_rate": 48000,
                        "generation_time": 0.5,
                        "audio_path": str(wav_file),
                    })
                return httpx.Response(404)

            client = LocalTTSClient(
                LocalTTSSettings("https://tts.example.test/", 120),
                transport=httpx.MockTransport(handler),
            )
            try:
                meta = client.generate("seg_1", "SPK_01", "ready", "Test fallback", 1.0)
                self.assertNotIn("audio_url", meta)
                data = client.download_audio(audio_path=meta.get("audio_path"))
                self.assertEqual(data, WAV_BYTES)
            finally:
                client.close()


if __name__ == "__main__":
    unittest.main()
