"""Global AI preferences. Secrets are only stored in the OS credential vault."""
import json
import os
from dataclasses import dataclass, asdict
from pathlib import Path
import keyring
from dotenv import dotenv_values
from cartoon_sub.project.cache import atomic_json
from cartoon_sub.tts.cache_identity import normalize_local_tts_base_url


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
    transcription_model: str = "gemini-3.5-flash"
    translation_model: str = "gemini-3.5-flash"
    translation_chunk_size: int = 40
    retry_count: int = 2
    translation_provider: str = "gemini"
    gemini_api_keys_file: str = ""

    def validate(self):
        for model in (self.transcription_model, self.translation_model):
            if not isinstance(model, str) or not model.strip() or any(c.isspace() for c in model):
                raise ValueError("Model không được rỗng hoặc chứa khoảng trắng")
        if type(self.translation_chunk_size) is not int or not 30 <= self.translation_chunk_size <= 50:
            raise ValueError("Translation chunk size phải từ 30 đến 50")
        if type(self.retry_count) is not int or not 0 <= self.retry_count <= 5:
            raise ValueError("Retry count phải từ 0 đến 5")
        from cartoon_sub.ai.text_client import PROVIDERS
        if self.translation_provider not in {"gemini", *PROVIDERS}:
            raise ValueError("Provider dịch không hợp lệ")
        if not isinstance(self.gemini_api_keys_file, str):
            raise ValueError("Đường dẫn file Gemini API keys không hợp lệ")
        self.gemini_api_keys_file = self.gemini_api_keys_file.strip()
        return self


class SettingsStore:
    service = "CartoonSub.Gemini"
    account = "api-key"

    def __init__(self, folder=None, vault=None, env_path=None):
        self.folder = Path(folder) if folder else Path.home() / ".cartoon_sub"
        self.vault = vault if vault is not None else keyring
        self.env_path = Path(env_path) if env_path else Path.cwd() / ".env"

    def load(self):
        path = self.folder / "settings.json"
        if not path.exists():
            return AISettings()
        try:
            return AISettings(**json.loads(path.read_text(encoding="utf-8"))).validate()
        except (ValueError, TypeError) as exc:
            raise ValueError("settings.json không hợp lệ; sửa hoặc đổi tên file rồi mở lại Settings") from exc

    def save(self, settings, new_key="", translation_key=""):
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
        atomic_json(self.folder / "settings.json", asdict(settings))

    def get_key(self, provider="gemini"):
        try:
            key = self.vault.get_password(self.service if provider == "gemini" else f"CartoonSub.{provider}", self.account)
        except Exception:
            key = None
        env_name = f"{provider.upper()}_API_KEY" if provider != "gemini" else "GEMINI_API_KEY"
        key = key or os.environ.get(env_name) or dotenv_values(self.env_path).get(env_name)
        if not key or not key.strip():
            raise ValueError(f"Chưa có {provider} API key. Mở Settings > AI, nhập key và bấm Lưu.")
        return key.strip()

    def get_gemini_keys(self, settings=None):
        from cartoon_sub.ai.gemini_client import load_gemini_keys
        settings = settings or self.load()
        if settings.gemini_api_keys_file:
            return load_gemini_keys(settings.gemini_api_keys_file)
        return [self.get_key("gemini")]

    def key_status(self, provider="gemini"):
        try:
            self.get_key(provider)
            return f"Đã có {provider} key trong keyring hoặc biến môi trường. Để trống ô key để giữ nguyên."
        except ValueError:
            return f"Chưa cấu hình {provider} key."

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
