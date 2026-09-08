"""Global AI preferences. Secrets are only stored in the OS credential vault."""
import json
import os
from dataclasses import dataclass, asdict
from pathlib import Path
import keyring
from dotenv import dotenv_values
from cartoon_sub.project.cache import atomic_json


@dataclass
class AISettings:
    transcription_model: str = "gemini-3.5-flash"
    translation_model: str = "gemini-3.5-flash"
    translation_chunk_size: int = 40
    retry_count: int = 2

    def validate(self):
        for model in (self.transcription_model, self.translation_model):
            if not isinstance(model, str) or not model.strip() or any(c.isspace() for c in model):
                raise ValueError("Model không được rỗng hoặc chứa khoảng trắng")
        if type(self.translation_chunk_size) is not int or not 30 <= self.translation_chunk_size <= 50:
            raise ValueError("Translation chunk size phải từ 30 đến 50")
        if type(self.retry_count) is not int or not 0 <= self.retry_count <= 5:
            raise ValueError("Retry count phải từ 0 đến 5")
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

    def save(self, settings, new_key=""):
        settings.validate()
        if new_key.strip():
            try:
                self.vault.set_password(self.service, self.account, new_key.strip())
            except Exception:
                raise RuntimeError("Không lưu được key vào OS keyring. Có thể cấu hình GEMINI_API_KEY trong .env cho môi trường dev.") from None
        atomic_json(self.folder / "settings.json", asdict(settings))

    def get_key(self):
        try:
            key = self.vault.get_password(self.service, self.account)
        except Exception:
            key = None
        key = key or os.environ.get("GEMINI_API_KEY") or dotenv_values(self.env_path).get("GEMINI_API_KEY")
        if not key or not key.strip():
            raise ValueError("Chưa có Gemini API key. Mở Settings > AI, nhập key và bấm Lưu.")
        return key.strip()

    def key_status(self):
        try:
            self.get_key()
            return "Đã có key (keyring hoặc GEMINI_API_KEY). Để trống ô key để giữ nguyên."
        except ValueError:
            return "Chưa cấu hình key. Nhập key lấy từ Google AI Studio."

    def load_dubbing(self):
        from cartoon_sub.syllable.target import DubbingSettings
        path=self.folder/"dubbing.json"
        return DubbingSettings(**json.loads(path.read_text(encoding="utf-8"))).validate() if path.exists() else DubbingSettings()

    def save_dubbing(self, settings):
        atomic_json(self.folder/"dubbing.json",settings.validate().to_dict())
