from __future__ import annotations

import logging
from pathlib import Path
import subprocess
import sys
import time

import httpx

from cartoon_sub.app.settings import LocalTTSSettings
from cartoon_sub.media.process import CancelledError

logger = logging.getLogger(__name__)


class LocalTTSProcessManager:
    def __init__(self, repo_root: Path | None = None):
        self.repo_root = repo_root or Path(__file__).resolve().parents[3]
        self.owned_process: subprocess.Popen | None = None
        self._log_handle = None

    @staticmethod
    def is_api_ready(base_url: str = "http://127.0.0.1:8765") -> bool:
        try:
            url = base_url.rstrip("/") + "/api/health"
            with httpx.Client(timeout=1.0) as client:
                resp = client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("status") == "READY"
        except Exception:
            pass
        return False

    def find_executable(self, configured_path: str | None = None) -> Path | None:
        if configured_path:
            p = Path(configured_path)
            if p.is_file():
                return p.resolve()

        root = self.repo_root
        workspace = root.parent
        candidates = [
            root / "Local_TTS" / "dist" / "Local_TTS" / "Local_TTS.exe",
            root / "Local_TTS" / "Local_TTS.exe",
            workspace / "TTS_Sub" / "Local_TTS" / "dist" / "Local_TTS" / "Local_TTS.exe",
            workspace / "TTS_Sub" / "Local_TTS" / "Local_TTS.exe",
            workspace / "Local_TTS" / "dist" / "Local_TTS" / "Local_TTS.exe",
            workspace / "Local_TTS" / "Local_TTS.exe",
            workspace / "TTS_Sub" / "Local_TTS" / ".venv" / "Scripts" / "python.exe",
            workspace / "Local_TTS" / ".venv" / "Scripts" / "python.exe",
        ]
        for c in candidates:
            if c.is_file():
                return c.resolve()
        return None

    def start(self, executable: Path | str, base_url: str = "http://127.0.0.1:8765") -> subprocess.Popen:
        exe_path = Path(executable).resolve()
        if not exe_path.is_file():
            raise FileNotFoundError(f"LOCAL_TTS_EXECUTABLE_NOT_FOUND: {exe_path}")

        if exe_path.name.lower() in {"python.exe", "pythonw.exe"}:
            cmd = [str(exe_path), "-m", "local_tts", "serve"]
            cwd = exe_path.parents[2]
        else:
            cmd = [str(exe_path), "serve"]
            cwd = exe_path.parent

        log_dir = Path.home() / ".cartoon_sub" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "local_tts_process.log"
        self._log_handle = log_file.open("a", encoding="utf-8")

        flags = 0
        if sys.platform == "win32":
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

        logger.info("Spawning Local_TTS: %s (cwd=%s)", cmd, cwd)
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            stdout=self._log_handle,
            stderr=subprocess.STDOUT,
            creationflags=flags,
        )
        self.owned_process = proc
        return proc

    def wait_until_ready(self, base_url: str, timeout_seconds: float = 120.0, cancel=None, progress=None) -> bool:
        start_time = time.monotonic()
        while time.monotonic() - start_time < timeout_seconds:
            if cancel and cancel.is_set():
                raise CancelledError("Local_TTS startup cancelled")
            if self.owned_process is not None:
                ret = self.owned_process.poll()
                if ret is not None:
                    raise RuntimeError(f"LOCAL_TTS_PROCESS_EXITED: Local_TTS process exited early with code {ret}")
            if self.is_api_ready(base_url):
                return True
            if progress:
                elapsed = int(time.monotonic() - start_time)
                progress(f"Đang khởi động Local_TTS ({elapsed}s)...")
            time.sleep(0.5)
        raise TimeoutError(f"LOCAL_TTS_STARTUP_TIMEOUT: Quá thời gian chờ Local_TTS khởi động ({timeout_seconds}s)")

    def ensure_running(self, settings: LocalTTSSettings, cancel=None, progress=None) -> tuple[bool, str]:
        if self.is_api_ready(settings.base_url):
            return True, "ALREADY_RUNNING"
        if not settings.auto_start_local_tts:
            return False, "AUTO_START_DISABLED"

        exe = self.find_executable(settings.local_tts_executable)
        if not exe:
            raise FileNotFoundError("LOCAL_TTS_EXECUTABLE_NOT_FOUND")

        if progress:
            progress(f"Đang khởi động Local_TTS ({exe.name})...")
        self.start(exe, settings.base_url)
        self.wait_until_ready(settings.base_url, timeout_seconds=settings.startup_timeout_seconds, cancel=cancel, progress=progress)
        return True, "STARTED"

    def shutdown_owned_process(self) -> None:
        if self.owned_process is not None:
            proc = self.owned_process
            self.owned_process = None
            if proc.poll() is None:
                try:
                    proc.terminate()
                    proc.wait(timeout=3.0)
                except (subprocess.TimeoutExpired, OSError):
                    try:
                        proc.kill()
                    except OSError:
                        pass
        if self._log_handle and not self._log_handle.closed:
            try:
                self._log_handle.close()
            except OSError:
                pass
