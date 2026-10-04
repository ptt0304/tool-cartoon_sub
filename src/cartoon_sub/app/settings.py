"""Global AI preferences. Secrets are only stored in the OS credential vault."""
import json
import os
import re
import tempfile
from threading import Lock
from dataclasses import dataclass, asdict, fields
from pathlib import Path
import keyring
from dotenv import dotenv_values
from cartoon_sub.project.cache import atomic_json
from cartoon_sub.tts.cache_identity import normalize_local_tts_base_url


APP_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_API_KEY_PATH = APP_ROOT / "api_key.txt"
OPENROUTER_KEY_NAME = "openrouter_key"


def parse_openrouter_key_file(path):
    """Return (explicit, ordered keys) without treating other providers as OpenRouter."""
    from cartoon_sub.ai.openrouter_client import parse_openrouter_api_keys
    path = Path(path)
    if not path.is_file():
        return False, []
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError):
        raise ValueError("Không đọc được file API key.") from None
    explicit, values = False, []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            name, value = (part.strip() for part in line.split(":", 1))
            if re.fullmatch(r"openrouter_key(?:_\d+)?", name.casefold()):
                explicit = True
                if value.casefold() not in {"", "null", "none", '""', "''"}:
                    values.append(value)
        elif line.startswith("sk-or-"):
            # Compatibility with this installation's historical single raw key.
            explicit = True
            values.append(line)
    return explicit, parse_openrouter_api_keys("\n".join(values))


def save_openrouter_key_file(path, keys):
    """Atomically replace only OpenRouter entries, preserving unrelated content."""
    from cartoon_sub.ai.openrouter_client import parse_openrouter_api_keys
    path = Path(path)
    keys = parse_openrouter_api_keys("\n".join(keys or []))
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines() if path.is_file() else []
    except (OSError, UnicodeError):
        raise ValueError("Không đọc được file API key.") from None
    preserved, insert_at = [], None
    for raw in lines:
        line = raw.strip()
        is_openrouter = False
        if line and not line.startswith("#"):
            if ":" in line:
                name = line.split(":", 1)[0].strip().casefold()
                is_openrouter = bool(re.fullmatch(r"openrouter_key(?:_\d+)?", name))
            elif line.startswith("sk-or-"):
                is_openrouter = True
        if is_openrouter:
            if insert_at is None:
                insert_at = len(preserved)
        else:
            preserved.append(raw)
    if insert_at is None:
        insert_at = len(preserved)
    replacement = ([f"{OPENROUTER_KEY_NAME}: {key}" for key in keys]
                   or [f"{OPENROUTER_KEY_NAME}:"])
    output = preserved[:insert_at] + replacement + preserved[insert_at:]
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join(output).rstrip("\n") + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return keys


@dataclass
class LocalTTSSettings:
    base_url: str = "http://127.0.0.1:8765"
    timeout_seconds: int = 300
    auto_start_local_tts: bool = True
    local_tts_executable: str | None = None
    startup_timeout_seconds: int = 120
    stop_local_tts_on_exit: bool = True

    def validate(self):
        try:
            self.base_url = normalize_local_tts_base_url(self.base_url)
        except ValueError as exc:
            raise ValueError("Local_TTS URL phải bắt đầu bằng http:// hoặc https:// và có host hợp lệ") from exc
        if type(self.timeout_seconds) is not int or not 10 <= self.timeout_seconds <= 1800:
            raise ValueError("Local_TTS timeout phải từ 10 đến 1800 giây")
        if type(self.startup_timeout_seconds) not in (int, float) or not 5 <= self.startup_timeout_seconds <= 600:
            raise ValueError("Local_TTS startup timeout phải từ 5 đến 600 giây")
        self.startup_timeout_seconds = int(self.startup_timeout_seconds)
        if self.local_tts_executable is not None:
            self.local_tts_executable = str(self.local_tts_executable).strip() or None
        self.auto_start_local_tts = bool(self.auto_start_local_tts)
        self.stop_local_tts_on_exit = bool(self.stop_local_tts_on_exit)
        return self


@dataclass
class AISettings:
    default_ai_model: str = ""
    tab_model_overrides: dict = None
    ai_model_routing_version: int = 1
    transcription_model: str = "gemini-3.8-flash"
    translation_model: str = ""
    vision_speaker_model: str = ""
    vision_input_mode: str = "frames"
    review_model_mode: str = "translation"
    review_model: str = ""
    translation_chunk_size: int = 40
    retry_count: int = 2
    translation_provider: str = "openrouter"
    gemini_api_keys_file: str = ""
    api_key_file: str = ""
    provider_filter: str = "all"
    transcription_provider: str = "gemini"
    legacy_gemini_video_model: str = "gemini-3.8-flash"
    legacy_gemini_audio_model: str = "gemini-3.8-flash"
    ui_zoom_percent: int = 100

    def validate(self):
        if self.tab_model_overrides is None:
            self.tab_model_overrides = {}
        if not isinstance(self.tab_model_overrides, dict):
            raise ValueError("Cấu hình model AI theo tab không hợp lệ")
        allowed_tabs = {"transcript", "transcript_stt", "translate", "subtitle", "audio"}
        cleaned = {}
        for tab, model in self.tab_model_overrides.items():
            if tab not in allowed_tabs or not isinstance(model, str) or any(c.isspace() for c in model):
                raise ValueError("Cấu hình model AI theo tab không hợp lệ")
            if model.strip():
                cleaned[tab] = model.strip()
        self.tab_model_overrides = cleaned
        if not isinstance(self.default_ai_model, str) or any(c.isspace() for c in self.default_ai_model):
            raise ValueError("Model AI mặc định không hợp lệ")
        self.default_ai_model = self.default_ai_model.strip()
        for label, model in (("transcription", self.transcription_model),
                             ("dịch", self.translation_model),
                             ("vision", self.vision_speaker_model),
                             ("QA", self.review_model)):
            if not isinstance(model, str) or any(c.isspace() for c in model):
                raise ValueError(f"Model {label} không hợp lệ")
        if self.translation_provider != "openrouter" and not self.translation_model:
            raise ValueError("Model dịch không được rỗng")
        if self.vision_input_mode not in {"frames", "video"}:
            raise ValueError("Visual Input không hợp lệ")
        if self.review_model_mode not in {"translation", "separate"}:
            raise ValueError("Review model mode không hợp lệ")
        if self.review_model_mode == "separate" and not self.review_model:
            raise ValueError("Review model không được rỗng khi dùng model riêng")
        if type(self.translation_chunk_size) is not int or not 30 <= self.translation_chunk_size <= 50:
            raise ValueError("Translation chunk size phải từ 30 đến 50")
        if type(self.retry_count) is not int or not 0 <= self.retry_count <= 5:
            raise ValueError("Retry count phải từ 0 đến 5")
        if type(self.ui_zoom_percent) is not int or not 0 <= self.ui_zoom_percent <= 100:
            raise ValueError("Thu phóng giao diện phải từ 0 đến 100%")
        from cartoon_sub.ai.text_client import PROVIDER_CATALOG
        if self.translation_provider not in PROVIDER_CATALOG:
            raise ValueError("Provider dịch không hợp lệ")
        if self.transcription_provider not in PROVIDER_CATALOG:
            raise ValueError("Provider transcription không hợp lệ")
        if (not isinstance(self.provider_filter, str) or not self.provider_filter
                or any(c.isspace() for c in self.provider_filter)):
            raise ValueError("Bộ lọc provider không hợp lệ")
        if not isinstance(self.gemini_api_keys_file, str):
            raise ValueError("Đường dẫn file Gemini API keys không hợp lệ")
        if not isinstance(self.api_key_file, str):
            raise ValueError("Đường dẫn file API keys không hợp lệ")
        self.gemini_api_keys_file = self.gemini_api_keys_file.strip()
        self.api_key_file = self.api_key_file.strip()
        return self

    def resolved_review_model(self):
        return self.translation_model if self.review_model_mode == "translation" else self.review_model


def load_api_keys(path):
    from cartoon_sub.ai.text_client import PROVIDER_CATALOG
    path = Path(path)
    if not path.is_file():
        raise ValueError("Không tìm thấy file API key.")
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError):
        raise ValueError("Không đọc được file API key.") from None
    by_name = {item["key_name"]: provider for provider, item in PROVIDER_CATALOG.items()}
    result = {}
    disabled = {"", "null", "none", '""', "''"}
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        name, value = (part.strip() for part in line.split(":", 1))
        provider = by_name.get(name.casefold())
        if provider:
            result[provider] = None if value.casefold() in disabled else value
    return result


class SettingsStore:
    service = "CartoonSub.Gemini"
    openrouter_service = "CartoonSub.OpenRouter"
    legacy_openrouter_service = "CartoonSub.openrouter"
    account = "api-key"
    openrouter_credentials_version = 1

    def __init__(self, folder=None, vault=None, env_path=None, api_key_path=None):
        custom_folder = folder is not None
        self.folder = Path(folder) if folder else Path.home() / ".cartoon_sub"
        self.vault = vault if vault is not None else keyring
        self.env_path = Path(env_path) if env_path else (
            self.folder / ".env" if custom_folder else APP_ROOT / ".env")
        self.api_key_path = Path(api_key_path) if api_key_path else (
            self.folder / "api_key.txt" if custom_folder else DEFAULT_API_KEY_PATH)
        self._catalog_sync_lock = Lock()
        self._catalog_sync_state = "idle"
        self._catalog_sync_error = ""
        self._openrouter_pool = None
        self._openrouter_pool_lock = Lock()
        from cartoon_sub.ai.openrouter_client import OpenRouterKeyPool
        self._openrouter_pool = OpenRouterKeyPool(self.get_openrouter_keys())

    def load(self):
        path = self.folder / "settings.json"
        if not path.exists():
            return AISettings()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if "gemini_keys_file" in data and "gemini_api_keys_file" not in data:
                data["gemini_api_keys_file"] = data["gemini_keys_file"]
            if "legacy_gemini_video_model" not in data and data.get("transcription_provider") == "gemini":
                data["legacy_gemini_video_model"] = data.get("transcription_model", "gemini-3.8-flash")
            if "legacy_gemini_audio_model" not in data and data.get("transcription_provider") == "gemini":
                data["legacy_gemini_audio_model"] = data.get("transcription_model", "gemini-3.8-flash")
            allowed = {item.name for item in fields(AISettings)}
            explicit_new_default = "default_ai_model" in data
            settings = AISettings(**{key: value for key, value in data.items() if key in allowed}).validate()
            cached = self.openrouter_catalog_cache()
            if settings.translation_provider != "openrouter" and cached:
                migrated = self._legacy_openrouter_model(
                    settings.translation_provider, settings.translation_model, cached["models"])
                if migrated:
                    settings.translation_provider = "openrouter"
                    settings.translation_model = migrated
            if not explicit_new_default and not settings.default_ai_model:
                available = {item.get("id") for item in (cached or {}).get("models", [])}
                migrated = next((candidate for candidate in (
                    settings.translation_model, settings.vision_speaker_model
                ) if candidate and candidate in available), "")
                settings.default_ai_model = migrated
                settings.ai_model_routing_version = 1
                atomic_json(path, asdict(settings.validate()))
            return settings
        except (ValueError, TypeError) as exc:
            raise ValueError("settings.json không hợp lệ; sửa hoặc đổi tên file rồi mở lại Settings") from exc

    @staticmethod
    def _legacy_openrouter_model(provider, model, models):
        available = {item["id"] for item in models}
        candidates = [model] if "/" in model else []
        prefixes = {"gemini": "google", "openai": "openai", "anthropic": "anthropic",
                    "deepseek": "deepseek"}
        if provider in prefixes:
            candidates.append(f"{prefixes[provider]}/{model}")
        return next((candidate for candidate in candidates if candidate in available), None)

    def save(self, settings, new_key="", translation_key="", openrouter_key="",
             openrouter_keys=None):
        settings.validate()
        if new_key.strip():
            try:
                self.vault.set_password(self.service, self.account, new_key.strip())
            except Exception:
                raise RuntimeError("Không lưu được key vào OS keyring. Có thể cấu hình GEMINI_API_KEY trong .env cho môi trường dev.") from None
        if translation_key.strip() and settings.translation_provider != "gemini":
            try:
                self.vault.set_password(f"CartoonSub.{settings.translation_provider}", self.account, translation_key.strip())
            except Exception:
                raise RuntimeError("Không lưu được translation API key vào OS keyring.") from None
        if openrouter_keys is not None or openrouter_key.strip():
            from cartoon_sub.ai.openrouter_client import parse_openrouter_api_keys
            keys = (parse_openrouter_api_keys("\n".join(openrouter_keys))
                    if openrouter_keys is not None else parse_openrouter_api_keys(openrouter_key))
            try:
                keys = save_openrouter_key_file(self.api_key_path, keys)
                from cartoon_sub.ai.openrouter_client import OpenRouterKeyPool
                with self._openrouter_pool_lock:
                    self._openrouter_pool = OpenRouterKeyPool(keys)
            except Exception:
                raise RuntimeError("Không lưu được OpenRouter API keys vào api_key.txt.") from None
        atomic_json(self.folder / "settings.json", asdict(settings))

    def save_ui_zoom(self, percent):
        settings = self.load()
        settings.ui_zoom_percent = int(percent)
        atomic_json(self.folder / "settings.json", asdict(settings.validate()))

    def set_tab_model_override(self, tab, model_id):
        settings = self.load()
        overrides = dict(settings.tab_model_overrides or {})
        if str(model_id or "").strip():
            overrides[tab] = str(model_id).strip()
        else:
            overrides.pop(tab, None)
        settings.tab_model_overrides = overrides
        atomic_json(self.folder / "settings.json", asdict(settings.validate()))

    def get_key(self, provider="gemini"):
        if provider == "openrouter":
            return self.openrouter_key_pool().current_key()
        settings = self.load()
        if settings.api_key_file and provider != "openrouter":
            return self.get_api_key(provider, settings)
        try:
            service = (self.service if provider == "gemini" else
                       self.openrouter_service if provider == "openrouter" else f"CartoonSub.{provider}")
            key = self.vault.get_password(service, self.account)
        except Exception:
            key = None
        env_name = f"{provider.upper()}_API_KEY" if provider != "gemini" else "GEMINI_API_KEY"
        key = key or os.environ.get(env_name) or dotenv_values(self.env_path).get(env_name)
        if (not key or not key.strip()) and settings.api_key_file:
            key = load_api_keys(settings.api_key_file).get(provider)
        if not key or not key.strip():
            raise ValueError(f"Chưa có {provider} API key trong File API keys.")
        return key.strip()

    @staticmethod
    def _decode_openrouter_credentials(stored):
        from cartoon_sub.ai.openrouter_client import parse_openrouter_api_keys
        if stored is None:
            return False, []
        if not isinstance(stored, str):
            return False, []
        try:
            decoded = json.loads(stored)
        except (ValueError, TypeError):
            decoded = stored
        if isinstance(decoded, dict):
            if decoded.get("version") != SettingsStore.openrouter_credentials_version:
                return True, []
            values = decoded.get("keys", [])
            if not isinstance(values, list):
                return True, []
            return True, parse_openrouter_api_keys("\n".join(str(item) for item in values))
        if isinstance(decoded, list):
            return True, parse_openrouter_api_keys("\n".join(str(item) for item in decoded))
        if isinstance(decoded, str):
            return True, parse_openrouter_api_keys(decoded)
        return True, []

    def _stored_openrouter_pool(self):
        try:
            stored = self.vault.get_password(self.openrouter_service, self.account)
        except Exception:
            stored = None
        exists, keys = self._decode_openrouter_credentials(stored)
        if exists:
            try:
                decoded = json.loads(stored)
                versioned = (isinstance(decoded, dict)
                             and decoded.get("version") == self.openrouter_credentials_version
                             and isinstance(decoded.get("keys"), list))
            except (ValueError, TypeError):
                versioned = False
            if not versioned:
                try:
                    self.vault.set_password(self.openrouter_service, self.account,
                        json.dumps({"version": self.openrouter_credentials_version, "keys": keys},
                                   ensure_ascii=False))
                except Exception:
                    pass
            return True, keys
        # Before the ordered pool existed, get_key("openrouter") used the
        # provider-derived, lower-case service name.
        try:
            legacy = self.vault.get_password(self.legacy_openrouter_service, self.account)
        except Exception:
            legacy = None
        legacy_exists, keys = self._decode_openrouter_credentials(legacy)
        if legacy_exists:
            try:
                self.vault.set_password(self.openrouter_service, self.account,
                    json.dumps({"version": self.openrouter_credentials_version, "keys": keys},
                               ensure_ascii=False))
            except Exception:
                pass  # Reading the legacy credential remains sufficient.
            return True, keys
        return False, []

    def get_stored_openrouter_keys(self):
        return self._stored_openrouter_pool()[1]

    def get_openrouter_keys(self):
        """Canonical file first, then keyring compatibility, then environment."""
        from cartoon_sub.ai.openrouter_client import parse_openrouter_api_keys
        explicit, keys = parse_openrouter_key_file(self.api_key_path)
        if explicit:
            return keys
        persisted, keys = self._stored_openrouter_pool()
        if persisted:
            return keys
        env_key = os.environ.get("OPENROUTER_API_KEY") or dotenv_values(self.env_path).get("OPENROUTER_API_KEY")
        return parse_openrouter_api_keys(env_key or "")

    def openrouter_key_source(self):
        explicit, keys = parse_openrouter_key_file(self.api_key_path)
        if explicit:
            return "file" if keys else "file_empty"
        persisted, keys = self._stored_openrouter_pool()
        if persisted:
            return "stored" if keys else "none"
        if os.environ.get("OPENROUTER_API_KEY") or dotenv_values(self.env_path).get("OPENROUTER_API_KEY"):
            return "environment"
        return "none"

    def openrouter_key_pool(self):
        with self._openrouter_pool_lock:
            return self._openrouter_pool

    def get_api_key(self, provider, settings=None):
        settings = settings or self.load()
        keys = load_api_keys(settings.api_key_file)
        key = keys.get(provider)
        if not key:
            raise ValueError(f"Provider {provider} chưa được cấu hình trong File API keys.")
        return key

    def get_gemini_keys(self, settings=None):
        from cartoon_sub.ai.gemini_client import load_gemini_keys
        settings = settings or self.load()
        if settings.api_key_file:
            return [self.get_api_key("gemini", settings)]
        if settings.gemini_api_keys_file:
            return load_gemini_keys(settings.gemini_api_keys_file)
        return [self.get_key("gemini")]

    def available_api_providers(self, settings=None):
        settings = settings or self.load()
        if not settings.api_key_file:
            return []
        return [provider for provider, key in load_api_keys(settings.api_key_file).items() if key]

    def key_status(self, provider="gemini"):
        try:
            if provider == "openrouter":
                count = len(self.get_openrouter_keys())
                return f"Đã cấu hình {count} OpenRouter key." if count else "Chưa cấu hình OpenRouter key."
            self.get_key(provider)
            return f"Đã có {provider} key trong keyring hoặc biến môi trường. Để trống ô key để giữ nguyên."
        except ValueError:
            return f"Chưa cấu hình {provider} key."

    def openrouter_catalog_cache(self):
        return self.openrouter_catalog_cache_for("general")

    def openrouter_catalog_cache_for(self, kind):
        from cartoon_sub.ai.openrouter_client import OpenRouterCatalog
        transcription = kind == "transcription"
        filename = "openrouter_transcription_models.json" if transcription else "openrouter_models.json"
        catalog = OpenRouterCatalog(self.folder / filename, self.openrouter_key_pool(),
                                    transcription=transcription)
        try:
            return catalog.load_cache()
        finally:
            catalog.close()

    def openrouter_catalog(self, *, refresh=False, cancel=None):
        from cartoon_sub.ai.openrouter_client import OpenRouterCatalog
        catalog = OpenRouterCatalog(self.folder / "openrouter_models.json", self.openrouter_key_pool())
        try:
            return catalog.get_models(refresh=refresh, cancel=cancel)
        finally:
            catalog.close()

    def catalog_sync_snapshot(self):
        general = self.openrouter_catalog_cache_for("general")
        transcription = self.openrouter_catalog_cache_for("transcription")
        return {
            "state": self._catalog_sync_state,
            "error": self._catalog_sync_error,
            "general": general,
            "transcription": transcription,
        }

    def sync_openrouter_catalogs_once(self, *, key_override="", retry=False,
                                      cancel=None, progress=None):
        """Synchronize both catalogs once for this application SettingsStore."""
        with self._catalog_sync_lock:
            if self._catalog_sync_state != "idle" and not (retry and self._catalog_sync_state == "offline"):
                return self.catalog_sync_snapshot()
            self._catalog_sync_state = "syncing"
        errors = []
        try:
            from cartoon_sub.ai.openrouter_client import OpenRouterCatalog
            credentials = (key_override.strip() if key_override.strip()
                           else self.openrouter_key_pool())
            for kind, filename, transcription in (
                ("general", "openrouter_models.json", False),
                ("transcription", "openrouter_transcription_models.json", True),
            ):
                if progress:
                    progress(f"Syncing OpenRouter {kind} catalog…")
                catalog = OpenRouterCatalog(self.folder / filename, credentials,
                                            transcription=transcription)
                try:
                    catalog.get_models(refresh=True, cancel=cancel)
                except Exception as exc:
                    errors.append(f"{kind}: {exc}")
                finally:
                    catalog.close()
            self._catalog_sync_state = "offline" if errors else "synced"
            self._catalog_sync_error = "; ".join(errors)
            return self.catalog_sync_snapshot()
        except Exception as exc:
            self._catalog_sync_state = "offline"
            self._catalog_sync_error = str(exc)
            return self.catalog_sync_snapshot()

    def load_dubbing(self):
        from cartoon_sub.syllable.target import DubbingSettings
        path=self.folder/"dubbing.json"
        return DubbingSettings(**json.loads(path.read_text(encoding="utf-8"))).validate() if path.exists() else DubbingSettings()

    def save_dubbing(self, settings):
        atomic_json(self.folder/"dubbing.json",settings.validate().to_dict())

    def load_local_tts(self):
        path = self.folder / "local_tts.json"
        if not path.exists():
            return LocalTTSSettings()
        try:
            return LocalTTSSettings(**json.loads(path.read_text(encoding="utf-8"))).validate()
        except (ValueError, TypeError) as exc:
            raise ValueError("local_tts.json không hợp lệ; sửa hoặc đổi tên file rồi mở lại") from exc

    def save_local_tts(self, settings):
        atomic_json(self.folder / "local_tts.json", asdict(settings.validate()))
