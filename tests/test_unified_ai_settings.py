import json
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import tempfile
import unittest
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
from PySide6.QtWidgets import QApplication

from cartoon_sub.ai.gemini_client import GeminiError
from cartoon_sub.ai.provider_errors import AIProviderError, ProviderErrorCategory
from cartoon_sub.ai.openrouter_client import (OPENROUTER_CHAT_URL, OPENROUTER_MODELS_URL,
    OPENROUTER_KEY_URL, OPENROUTER_TRANSCRIPTION_MODELS_URL, OpenRouterCatalog,
    OpenRouterClient, OpenRouterKeyPool, filter_models, mask_api_key, model_author,
    normalize_model_author, parse_models, parse_openrouter_api_keys, supports_capability,
    validate_openrouter_key)
from cartoon_sub.ai.openrouter_client import _parse_json_object_content
from cartoon_sub.ai.text_client import text_client_factory
from cartoon_sub.app.settings import (AISettings, SettingsStore, parse_openrouter_key_file,
                                      save_openrouter_key_file)
from cartoon_sub.project.cache import atomic_json
from cartoon_sub.media.process import CancelledError
from cartoon_sub.translation.requests import CachedRequests
from cartoon_sub.ui.settings_dialog import SettingsDialog


def general_payload():
    return {"data": [
        {"id": "google/vision-test", "name": "Gemini Vision", "context_length": 100000,
         "pricing": {"prompt": "0.000001", "completion": "0.000002"},
         "architecture": {"input_modalities": ["text", "image"], "output_modalities": ["text"]},
         "supported_parameters": ["temperature", "response_format"]},
        {"id": "qwen/text-test", "name": "Qwen Translator", "context_length": 200000,
         "pricing": {}, "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
         "supported_parameters": []},
        {"id": "openai/video-test", "name": "Video Model", "context_length": 32000,
         "pricing": {}, "architecture": {"input_modalities": ["text", "video"], "output_modalities": ["text"]},
         "supported_parameters": []},
        {"id": "~openai/alternate-text", "name": "Alternate OpenAI",
         "pricing": {}, "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]}},
        {"id": "openai/image-output", "name": "Image Output",
         "architecture": {"input_modalities": ["text"], "output_modalities": ["image"]}},
        {"id": "qwen/text-test", "name": "duplicate"},
    ]}


def transcription_payload():
    return {"data": [
        {"id": "openai/whisper-test", "name": "Whisper Test", "pricing": {"audio": "0.006"},
         "architecture": {"input_modalities": ["audio"], "output_modalities": ["transcription"]}},
        {"id": "microsoft/mai-transcribe", "name": "MAI Transcribe", "pricing": {"prompt": "0.1"},
         "architecture": {"input_modalities": ["audio"], "output_modalities": ["transcription"]}},
    ]}


class Response:
    def __init__(self, payload, status=200, url=OPENROUTER_MODELS_URL):
        self.payload, self.status_code = payload, status
        self.request = httpx.Request("GET", url); self.headers = {}

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("secret-bearing-sdk-message", request=self.request, response=self)


class MemoryVault:
    """Process-lifetime fake matching keyring's service/account contract."""
    def __init__(self):
        self.values = {}

    def set_password(self, service, account, value):
        self.values[(service, account)] = value

    def get_password(self, service, account):
        return self.values.get((service, account))


class UnifiedAISettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_store(self, root, settings=None):
        vault = Mock(); vault.get_password.return_value = "OPENROUTER-SECRET"
        store = SettingsStore(Path(root) / "settings", vault)
        general, transcription = parse_models(general_payload()), parse_models(transcription_payload())
        atomic_json(store.folder / "openrouter_models.json",
                    {"updated_at": "2026-10-01T00:00:00Z", "models": general})
        atomic_json(store.folder / "openrouter_transcription_models.json",
                    {"updated_at": "2026-10-01T00:00:00Z", "models": transcription})
        store.save(settings or AISettings(
            default_ai_model="qwen/text-test",
            transcription_model="openai/whisper-test", translation_model="qwen/text-test",
            vision_speaker_model="google/vision-test"))
        return store, general, transcription

    def test_model_author_and_catalog_parsing(self):
        models = parse_models(general_payload())
        self.assertEqual(model_author(models[0]), "google")
        self.assertEqual(normalize_model_author("openai/model"), "openai")
        self.assertEqual(normalize_model_author("~openai/model"), "openai")
        self.assertEqual(model_author(next(m for m in models if m["id"].startswith("~"))), "openai")
        self.assertEqual(len(models), 5)
        self.assertEqual(set(models[0]), {"id", "name", "context_length", "pricing", "architecture",
                                         "supported_parameters"})
        self.assertEqual(len(parse_models(transcription_payload())), 2)

    def test_structured_json_parser_accepts_reasoning_prefix_and_multipart(self):
        self.assertEqual(_parse_json_object_content(
            'Reasoning example {not json}. Final:\n```json\n{"ok": true}\n```'), {"ok": True})
        self.assertEqual(_parse_json_object_content([
            {"type": "text", "text": "prefix"},
            {"type": "output_text", "text": '{"ok": true}'},
        ]), {"ok": True})

    def test_task_capability_filters(self):
        models = parse_models(general_payload())
        self.assertEqual([m["id"] for m in filter_models(models, capability="translation")],
                         ["google/vision-test", "qwen/text-test", "openai/video-test",
                          "~openai/alternate-text"])
        self.assertEqual([m["id"] for m in filter_models(models, capability="vision_frames")],
                         ["google/vision-test"])
        self.assertEqual([m["id"] for m in filter_models(models, capability="vision_video")],
                         ["openai/video-test"])
        self.assertTrue(supports_capability(parse_models(transcription_payload())[0], "transcription"))

    def test_search_by_id_name_and_author(self):
        models = parse_models(general_payload())
        self.assertEqual(len(filter_models(models, search="qwen/text")), 1)
        self.assertEqual(len(filter_models(models, search="translator")), 1)
        self.assertEqual([m["id"] for m in filter_models(models, author="google")],
                         ["google/vision-test"])
        openai = [m["id"] for m in filter_models(models, author="openai")]
        self.assertEqual(openai, ["openai/video-test", "~openai/alternate-text"])
        self.assertEqual(next(m for m in models if m["id"].startswith("~"))["id"],
                         "~openai/alternate-text")

    def test_general_and_transcription_catalog_urls_and_cache(self):
        with tempfile.TemporaryDirectory() as root:
            http = Mock(); http.get.side_effect = [Response(general_payload()),
                Response(transcription_payload(), url=OPENROUTER_TRANSCRIPTION_MODELS_URL)]
            general = OpenRouterCatalog(Path(root) / "general.json", lambda: "SECRET", http)
            transcription = OpenRouterCatalog(Path(root) / "transcription.json", lambda: "SECRET", http,
                                              transcription=True)
            self.assertEqual(len(general.get_models(refresh=True)["models"]), 5)
            self.assertEqual(len(transcription.get_models(refresh=True)["models"]), 2)
            self.assertEqual(http.get.call_args_list[0].args[0], OPENROUTER_MODELS_URL)
            self.assertEqual(http.get.call_args_list[1].args[0], OPENROUTER_TRANSCRIPTION_MODELS_URL)

    def test_sync_failure_keeps_cache(self):
        with tempfile.TemporaryDirectory() as root:
            store, general, transcription = self.make_store(root)
            with patch.object(OpenRouterCatalog, "fetch", side_effect=GeminiError("offline")):
                snapshot = store.sync_openrouter_catalogs_once()
            self.assertEqual(snapshot["state"], "offline")
            self.assertEqual(snapshot["general"]["models"], general)
            self.assertEqual(snapshot["transcription"]["models"], transcription)

    def test_only_one_catalog_sync_per_store(self):
        with tempfile.TemporaryDirectory() as root:
            store = SettingsStore(Path(root), Mock())

            def fetched(catalog, cancel=None):
                del cancel
                rows = parse_models(transcription_payload() if catalog.transcription else general_payload())
                payload = {"updated_at": "now", "models": rows}
                atomic_json(catalog.cache_path, payload)
                return payload

            with patch.object(OpenRouterCatalog, "fetch", autospec=True, side_effect=fetched) as fetch:
                first = store.sync_openrouter_catalogs_once()
                second = store.sync_openrouter_catalogs_once()
            self.assertEqual(first["state"], "synced")
            self.assertEqual(second["state"], "synced")
            self.assertEqual(fetch.call_count, 2)  # general + transcription, once each

    def test_dialog_task_assignments_stale_and_review_inheritance(self):
        with tempfile.TemporaryDirectory() as root:
            stale = AISettings(default_ai_model="qwen/text-test",
                transcription_model="removed/stt", translation_model="qwen/text-test",
                vision_speaker_model="google/vision-test", review_model_mode="translation")
            store, _, _ = self.make_store(root, stale)
            dialog = SettingsDialog(SimpleNamespace(settings_store=store))
            self.assertEqual(dialog.transcription_model.currentData(), "openai/whisper-test")
            authors = [dialog.author.itemData(i) for i in range(dialog.author.count())]
            self.assertEqual(authors.count("openai"), 1)
            self.assertFalse(any(str(author).startswith("~") for author in authors))
            dialog.author_search.setText("goo")
            self.assertEqual([dialog.author.itemData(i) for i in range(dialog.author.count())],
                             ["all", "google"])
            dialog.author_search.clear()
            values = dialog.values()
            self.assertEqual(values.transcription_model, "openai/whisper-test")
            self.assertEqual(values.default_ai_model, "qwen/text-test")
            dialog.video_mode.setChecked(True)
            self.assertEqual(dialog.values().default_ai_model, "qwen/text-test")
            dialog.close()

    def test_settings_roundtrip_and_legacy_migration(self):
        with tempfile.TemporaryDirectory() as root:
            settings = AISettings(default_ai_model="qwen/text-test",
                transcription_model="openai/whisper-test",
                translation_model="qwen/text-test", vision_speaker_model="google/vision-test",
                vision_input_mode="frames", review_model_mode="separate", review_model="qwen/text-test")
            store, _, _ = self.make_store(root, settings)
            loaded = store.load()
            self.assertEqual(loaded.vision_speaker_model, "google/vision-test")
            self.assertEqual(loaded.resolved_review_model(), "qwen/text-test")

            store.save(AISettings(transcription_provider="gemini", transcription_model="gemini-3.8-flash",
                                  translation_provider="gemini", translation_model="vision-test"))
            loaded = store.load()
            self.assertEqual(loaded.translation_model, "google/vision-test")
            self.assertEqual(loaded.legacy_gemini_video_model, "gemini-3.8-flash")

    def test_chat_canonical_id_optional_parameters_and_factory(self):
        models = parse_models(general_payload())
        http = Mock(); http.post.return_value = Response(
            {"choices": [{"message": {"content": '{"ok": true}'}}]}, url=OPENROUTER_CHAT_URL)
        client = OpenRouterClient("SECRET", models, http)
        self.assertEqual(client.generate_json("system", "prompt", {"type": "object"},
                                              "google/vision-test"), {"ok": True})
        body = http.post.call_args.kwargs["json"]
        self.assertEqual(body["model"], "google/vision-test")
        self.assertIn("temperature", body); self.assertIn("response_format", body)
        client.generate_json("system", "prompt", {"type": "object"}, "qwen/text-test")
        body = http.post.call_args.kwargs["json"]
        self.assertNotIn("temperature", body); self.assertNotIn("response_format", body)
        self.assertIsInstance(text_client_factory("openrouter")("SECRET"), OpenRouterClient)

    def test_key_parsing_masking_and_pool_order(self):
        keys = parse_openrouter_api_keys("  key-A\n\nkey-B\nkey-A\n key-C ")
        self.assertEqual(keys, ["key-A", "key-B", "key-C"])
        pool = OpenRouterKeyPool(keys)
        self.assertEqual(pool.current_key(), "key-A")
        pool.mark_auth_invalid("key-A")
        self.assertEqual(pool.current_key(), "key-B")
        pool.mark_temporary_failure("key-B")
        self.assertEqual(pool.current_key(), "key-C")
        self.assertNotIn("long-secret-value", mask_api_key("long-secret-value"))

    def test_runtime_pool_success_and_bounded_failover(self):
        success = {"choices": [{"message": {"content": '{"ok": true}'}}]}
        cases = (
            (401, ProviderErrorCategory.AUTH_INVALID, "AUTH_INVALID"),
            (429, ProviderErrorCategory.RATE_LIMITED, "TEMPORARILY_UNAVAILABLE"),
            (502, ProviderErrorCategory.SERVER_ERROR, "TEMPORARILY_UNAVAILABLE"),
        )
        for status, category, state in cases:
            with self.subTest(status=status):
                http = Mock()
                http.post.side_effect = [Response({}, status, OPENROUTER_CHAT_URL),
                                         Response(success, 200, OPENROUTER_CHAT_URL)]
                pool = OpenRouterKeyPool(["A", "B"], cooldown_seconds=60)
                client = OpenRouterClient(pool, client=http)
                self.assertEqual(client.generate_json("s", "p", {"type": "object"},
                                                      "test/model"), {"ok": True})
                self.assertEqual(http.post.call_count, 2)
                self.assertEqual(pool.state("A"), state)
                self.assertEqual(pool.state("B"), "AVAILABLE")
                first = http.post.call_args_list[0].kwargs["headers"]["Authorization"]
                second = http.post.call_args_list[1].kwargs["headers"]["Authorization"]
                self.assertEqual((first, second), ("Bearer A", "Bearer B"))
                if status != 401:
                    self.assertNotEqual(category, ProviderErrorCategory.AUTH_INVALID)

    def test_timeout_fails_over_and_bad_request_does_not_rotate(self):
        for failure in (httpx.TimeoutException("hidden"), httpx.ConnectError("hidden")):
            with self.subTest(failure=type(failure).__name__):
                success = Response({"choices": [{"message": {"content": '{"ok": true}'}}]},
                                   200, OPENROUTER_CHAT_URL)
                http = Mock(); http.post.side_effect = [failure, success]
                pool = OpenRouterKeyPool(["A", "B"], cooldown_seconds=60)
                self.assertEqual(OpenRouterClient(pool, client=http).generate_json(
                    "s", "p", {"type": "object"}, "test/model"), {"ok": True})
                self.assertEqual(http.post.call_count, 2)
                self.assertEqual(pool.state("A"), "TEMPORARILY_UNAVAILABLE")

        http = Mock(); http.post.return_value = Response({}, 400, OPENROUTER_CHAT_URL)
        pool = OpenRouterKeyPool(["A", "B"])
        with self.assertRaises(AIProviderError) as caught:
            OpenRouterClient(pool, client=http).generate_json(
                "s", "p", {"type": "object"}, "test/model")
        self.assertEqual(caught.exception.error_category, ProviderErrorCategory.BAD_REQUEST)
        self.assertEqual(http.post.call_count, 1)
        self.assertEqual(pool.state("A"), "AVAILABLE")

    def test_unconfirmed_403_is_model_error_without_rotation(self):
        http = Mock(); http.post.return_value = Response(
            {"error": {"message": "Model access denied for this account"}},
            403, OPENROUTER_CHAT_URL)
        pool = OpenRouterKeyPool(["A", "B"])
        with self.assertRaises(AIProviderError) as caught:
            OpenRouterClient(pool, client=http).generate_json(
                "s", "p", {"type": "object"}, "test/model")
        self.assertEqual(caught.exception.error_category, ProviderErrorCategory.MODEL_ERROR)
        self.assertEqual(http.post.call_count, 1)
        self.assertEqual(pool.state("A"), "AVAILABLE")

    def test_all_keys_unusable_is_bounded_and_cancellation_stops_failover(self):
        http = Mock(); http.post.side_effect = [
            Response({}, 401, OPENROUTER_CHAT_URL),
            Response({}, 429, OPENROUTER_CHAT_URL),
            Response({}, 503, OPENROUTER_CHAT_URL),
        ]
        pool = OpenRouterKeyPool(["A", "B", "C"])
        with self.assertRaisesRegex(AIProviderError, "No usable"):
            OpenRouterClient(pool, client=http).generate_json(
                "s", "p", {"type": "object"}, "test/model")
        self.assertEqual(http.post.call_count, 3)

        cancel = Event()
        def cancelled_request(*args, **kwargs):
            del args, kwargs
            cancel.set()
            raise httpx.TimeoutException("hidden")
        http = Mock(); http.post.side_effect = cancelled_request
        with self.assertRaises(CancelledError):
            OpenRouterClient(OpenRouterKeyPool(["A", "B"]), client=http).generate_json(
                "s", "p", {"type": "object"}, "test/model", cancel=cancel)
        self.assertEqual(http.post.call_count, 1)

    def test_temporary_cooldown_expires(self):
        now = [100.0]
        pool = OpenRouterKeyPool(["A", "B"], cooldown_seconds=10, clock=lambda: now[0])
        pool.mark_temporary_failure("A")
        self.assertEqual(pool.state("A"), "TEMPORARILY_UNAVAILABLE")
        now[0] = 111.0
        self.assertEqual(pool.state("A"), "AVAILABLE")

    def test_cache_identity_does_not_include_failover_key(self):
        class Store:
            def __init__(self, pool): self.pool = pool
            def openrouter_key_pool(self): return self.pool
            def openrouter_catalog_cache(self): return None

        success = {"choices": [{"message": {"content": '{"ok": true}'}}]}
        http = Mock(); http.post.side_effect = [Response({}, 401, OPENROUTER_CHAT_URL),
                                                Response(success, 200, OPENROUTER_CHAT_URL)]
        pool = OpenRouterKeyPool(["A", "B"])
        with tempfile.TemporaryDirectory() as root:
            factory = lambda credentials: OpenRouterClient(credentials, client=http)
            requests = CachedRequests(Store(pool), root, "test/model", 2,
                                      client_factory=factory, provider="openrouter")
            value, cache_key = requests.request("s", "p", {"type": "object"},
                lambda payload: payload, "test")
            requests.close()
            self.assertEqual(value, {"ok": True})
            self.assertEqual(http.post.call_count, 2)

            cached_http = Mock()
            cached = CachedRequests(Store(OpenRouterKeyPool(["B"])), root, "test/model", 2,
                client_factory=lambda credentials: OpenRouterClient(credentials, client=cached_http),
                provider="openrouter")
            value2, cache_key2 = cached.request("s", "p", {"type": "object"},
                lambda payload: payload, "test")
            self.assertEqual((value2, cache_key2), ({"ok": True}, cache_key))
            cached_http.post.assert_not_called()

    def test_key_pool_storage_migration_environment_and_priority(self):
        with tempfile.TemporaryDirectory() as root:
            vault = Mock(); vault.get_password.return_value = "legacy-single-key"
            store = SettingsStore(Path(root), vault)
            self.assertEqual(store.get_openrouter_keys(), ["legacy-single-key"])

            vault.get_password.return_value = None
            store = SettingsStore(Path(root), vault)
            with patch.dict(os.environ, {"OPENROUTER_API_KEY": "environment-key"}):
                self.assertEqual(store.get_openrouter_keys(), ["environment-key"])

            vault.get_password.return_value = '["stored-A", "stored-B", "stored-A"]'
            store = SettingsStore(Path(root), vault)
            with patch.dict(os.environ, {"OPENROUTER_API_KEY": "environment-key"}):
                self.assertEqual(store.get_openrouter_keys(), ["stored-A", "stored-B"])

            settings = AISettings(translation_model="qwen/text-test")
            store.save(settings, openrouter_keys=["A", "B", "A"])
            self.assertEqual(parse_openrouter_key_file(Path(root) / "api_key.txt"),
                             (True, ["A", "B"]))

    def test_key_pool_survives_settings_store_restart_and_initializes_runtime(self):
        with tempfile.TemporaryDirectory() as root:
            vault = MemoryVault()
            first = SettingsStore(Path(root), vault, Path(root) / ".env")
            first.save(AISettings(translation_model="qwen/text-test"),
                       openrouter_keys=["TEST-KEY-A", "TEST-KEY-B", "TEST-KEY-A"])
            restarted = SettingsStore(Path(root), vault, Path(root) / ".env")
            self.assertEqual(restarted.get_openrouter_keys(),
                             ["TEST-KEY-A", "TEST-KEY-B"])
            self.assertEqual(restarted.openrouter_key_pool().keys,
                             ["TEST-KEY-A", "TEST-KEY-B"])

    def test_legacy_lowercase_service_migrates_to_versioned_pool(self):
        with tempfile.TemporaryDirectory() as root:
            vault = MemoryVault()
            vault.set_password("CartoonSub.openrouter", "api-key", "TEST-LEGACY-A")
            restarted = SettingsStore(Path(root), vault, Path(root) / ".env")
            self.assertEqual(restarted.get_openrouter_keys(), ["TEST-LEGACY-A"])
            migrated = json.loads(vault.get_password("CartoonSub.OpenRouter", "api-key"))
            self.assertEqual(migrated, {"version": 1, "keys": ["TEST-LEGACY-A"]})

    def test_legacy_raw_value_in_current_service_is_migrated(self):
        with tempfile.TemporaryDirectory() as root:
            vault = MemoryVault()
            vault.set_password("CartoonSub.OpenRouter", "api-key", "TEST-LEGACY-A")
            store = SettingsStore(Path(root), vault, Path(root) / ".env")
            self.assertEqual(store.get_openrouter_keys(), ["TEST-LEGACY-A"])
            self.assertEqual(json.loads(vault.get_password("CartoonSub.OpenRouter", "api-key")),
                             {"version": 1, "keys": ["TEST-LEGACY-A"]})

    def test_explicit_pool_wins_over_environment_and_empty_tombstone_stays_empty(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(
                os.environ, {"OPENROUTER_API_KEY": "TEST-ENV-C"}):
            vault = MemoryVault(); settings = AISettings(translation_model="qwen/text-test")
            first = SettingsStore(Path(root), vault, Path(root) / ".env")
            first.save(settings, openrouter_keys=["TEST-KEY-A", "TEST-KEY-B"])
            self.assertEqual(SettingsStore(Path(root), vault, Path(root) / ".env").get_openrouter_keys(),
                             ["TEST-KEY-A", "TEST-KEY-B"])
            first.save(settings, openrouter_keys=[])
            restarted = SettingsStore(Path(root), vault, Path(root) / ".env")
            self.assertEqual(restarted.get_openrouter_keys(), [])
            self.assertEqual(restarted.openrouter_key_pool().keys, [])

    def test_environment_is_used_only_when_no_persisted_record_exists(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(
                os.environ, {"OPENROUTER_API_KEY": "TEST-ENV-C"}):
            store = SettingsStore(Path(root), MemoryVault(), Path(root) / ".env")
            self.assertEqual(store.get_openrouter_keys(), ["TEST-ENV-C"])

    def test_reopened_settings_reports_persisted_pool_without_mask_roundtrip(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root); vault = MemoryVault()
            store = SettingsStore(folder, vault, folder / ".env")
            atomic_json(folder / "openrouter_models.json",
                        {"updated_at": "now", "models": parse_models(general_payload())})
            atomic_json(folder / "openrouter_transcription_models.json",
                        {"updated_at": "now", "models": parse_models(transcription_payload())})
            settings = AISettings(default_ai_model="qwen/text-test",
                transcription_model="openai/whisper-test", translation_model="qwen/text-test",
                vision_speaker_model="google/vision-test")
            store.save(settings, openrouter_keys=["TEST-KEY-A", "TEST-KEY-B"])
            restarted = SettingsStore(folder, vault, folder / ".env")
            dialog = SettingsDialog(SimpleNamespace(settings_store=restarted))
            self.assertEqual(dialog.pending_keys, ["TEST-KEY-A", "TEST-KEY-B"])
            self.assertEqual(dialog.keys_summary.text(), "2 keys configured (api_key.txt)")
            self.assertFalse(dialog.keys_edited)
            with patch("cartoon_sub.ui.settings_dialog.validate_openrouter_key",
                       return_value={"status": "VALID", "message": "Valid"}) as validate, \
                 patch.object(restarted, "sync_openrouter_catalogs_once"):
                dialog._test_operation()
            self.assertEqual([call.args[0] for call in validate.call_args_list],
                             ["TEST-KEY-A", "TEST-KEY-B"])
            dialog.save()
            self.assertEqual(restarted.get_openrouter_keys(),
                             ["TEST-KEY-A", "TEST-KEY-B"])

    def test_api_key_file_parser_deduplicates_and_ignores_other_providers(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "api_key.txt"
            path.write_text(
                "# credentials\nopenrouter_key: TEST-A\nclaude_key: OTHER\n"
                "openrouter_key_2: TEST-B\nopenrouter_key: TEST-A\n",
                encoding="utf-8")
            self.assertEqual(parse_openrouter_key_file(path), (True, ["TEST-A", "TEST-B"]))

    def test_legacy_raw_openrouter_file_is_supported(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "api_key.txt"
            path.write_text("sk-or-TEST-LEGACY\n", encoding="utf-8")
            self.assertEqual(parse_openrouter_key_file(path), (True, ["sk-or-TEST-LEGACY"]))

    def test_api_key_file_wins_and_save_preserves_unrelated_entries(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(
                os.environ, {"OPENROUTER_API_KEY": "TEST-ENV-C"}):
            root = Path(root); vault = MemoryVault()
            vault.set_password("CartoonSub.OpenRouter", "api-key",
                               json.dumps({"version": 1, "keys": ["TEST-KEYRING-B"]}))
            path = root / "api_key.txt"
            path.write_text("gemini_key: KEEP-ME\nopenrouter_key: TEST-FILE-A\n",
                            encoding="utf-8")
            store = SettingsStore(root, vault, root / ".env")
            self.assertEqual(store.openrouter_key_pool().keys, ["TEST-FILE-A"])
            store.save(AISettings(translation_model="qwen/text-test"),
                       openrouter_keys=["TEST-NEW-A", "TEST-NEW-B"])
            text = path.read_text(encoding="utf-8")
            self.assertIn("gemini_key: KEEP-ME", text)
            self.assertEqual(parse_openrouter_key_file(path),
                             (True, ["TEST-NEW-A", "TEST-NEW-B"]))

    def test_different_working_directory_does_not_change_explicit_key_path(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as other:
            root = Path(root); path = root / "api_key.txt"
            save_openrouter_key_file(path, ["TEST-A"])
            previous = Path.cwd()
            try:
                os.chdir(other)
                with patch("cartoon_sub.app.settings.DEFAULT_API_KEY_PATH", path), \
                     patch("cartoon_sub.ai.openrouter_client.httpx.Client") as http:
                    store = SettingsStore(vault=MemoryVault(), env_path=root / ".env")
                    http.assert_not_called()
                self.assertEqual(store.openrouter_key_pool().keys, ["TEST-A"])
                self.assertEqual(store.api_key_path.resolve(), path.resolve())
            finally:
                os.chdir(previous)

    def test_authenticated_key_validation_statuses(self):
        cases = ((200, "VALID"), (401, "INVALID_OR_REVOKED"),
                 (403, "INVALID_OR_REVOKED"), (429, "RATE_LIMITED"),
                 (502, "SERVER_ERROR"), (503, "SERVER_ERROR"))
        for code, status in cases:
            with self.subTest(code=code):
                http = Mock(); http.get.return_value = Response({}, code, OPENROUTER_KEY_URL)
                self.assertEqual(validate_openrouter_key("EXACT-KEY", http)["status"], status)
                self.assertEqual(http.get.call_args.args[0], OPENROUTER_KEY_URL)
                self.assertEqual(http.get.call_args.kwargs["headers"]["Authorization"], "Bearer EXACT-KEY")

        http = Mock(); http.get.side_effect = httpx.TimeoutException("secret")
        self.assertEqual(validate_openrouter_key("EXACT-KEY", http)["status"], "TIMEOUT")
        http.get.side_effect = httpx.ConnectError("secret")
        self.assertEqual(validate_openrouter_key("EXACT-KEY", http)["status"], "NETWORK_ERROR")

    def test_public_models_success_does_not_validate_key(self):
        http = Mock(); http.get.side_effect = [Response(general_payload(), 200, OPENROUTER_MODELS_URL),
                                                Response({}, 401, OPENROUTER_KEY_URL)]
        client = OpenRouterClient("FAKE-TYPED-KEY", client=http)
        self.assertTrue(client.get_models())
        with self.assertRaisesRegex(GeminiError, "Invalid or revoked"):
            client.test_connection()
        self.assertEqual(http.get.call_args.args[0], OPENROUTER_KEY_URL)

    def test_dialog_tests_exact_edited_key_without_fallback(self):
        with tempfile.TemporaryDirectory() as root:
            store, _, _ = self.make_store(root)
            dialog = SettingsDialog(SimpleNamespace(settings_store=store))
            dialog.pending_keys = ["FAKE-TYPED-KEY"]; dialog.keys_edited = True
            with patch("cartoon_sub.ui.settings_dialog.validate_openrouter_key",
                       return_value={"status": "INVALID_OR_REVOKED", "message": "Invalid"}) as validate:
                results = dialog._test_operation()
            validate.assert_called_once_with("FAKE-TYPED-KEY")
            self.assertEqual(results[0]["status"], "INVALID_OR_REVOKED")
            dialog._show_key_results(results)
            self.assertNotIn("FAKE-TYPED-KEY", dialog.connection_status.text())

            dialog.pending_keys = ["INVALID-FIRST-KEY", "VALID-TYPED-KEY"]
            with patch("cartoon_sub.ui.settings_dialog.validate_openrouter_key",
                       side_effect=[{"status": "INVALID_OR_REVOKED", "message": "Invalid"},
                                    {"status": "VALID", "message": "Valid"}]) as validate, \
                 patch.object(store, "sync_openrouter_catalogs_once"):
                results = dialog._test_operation()
            self.assertEqual([call.args[0] for call in validate.call_args_list],
                             ["INVALID-FIRST-KEY", "VALID-TYPED-KEY"])
            self.assertEqual([row["status"] for row in results],
                             ["INVALID_OR_REVOKED", "VALID"])
            dialog.close()

    def test_key_never_appears_in_error_status_or_settings_json(self):
        secret = "OPENROUTER-VERY-SECRET"
        http = Mock(); http.get.return_value = Response({}, 401, OPENROUTER_KEY_URL)
        with self.assertRaises(GeminiError) as caught:
            OpenRouterClient(secret, client=http).test_connection()
        self.assertNotIn(secret, str(caught.exception))
        self.assertIn("Invalid or revoked", str(caught.exception))

        with tempfile.TemporaryDirectory() as root:
            vault = Mock(); store = SettingsStore(Path(root), vault)
            settings = AISettings(translation_model="qwen/text-test")
            store.save(settings, openrouter_key=secret)
            self.assertNotIn(secret, (Path(root) / "settings.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
