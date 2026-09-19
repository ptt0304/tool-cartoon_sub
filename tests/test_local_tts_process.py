import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PySide6.QtWidgets import QApplication

from cartoon_sub.app.settings import LocalTTSSettings
from cartoon_sub.tts.process_manager import LocalTTSProcessManager
from cartoon_sub.ui.main_window import MainWindow


class LocalTTSProcessManagerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_a_api_already_ready_no_subprocess_spawned(self):
        pm = LocalTTSProcessManager()
        settings = LocalTTSSettings(base_url="http://127.0.0.1:8765", auto_start_local_tts=True)
        with patch.object(pm, "is_api_ready", return_value=True), patch.object(pm, "start") as mock_start:
            ready, status = pm.ensure_running(settings)
            self.assertTrue(ready)
            self.assertEqual(status, "ALREADY_RUNNING")
            mock_start.assert_not_called()

    def test_b_api_down_executable_valid_spawns_once(self):
        pm = LocalTTSProcessManager()
        settings = LocalTTSSettings(base_url="http://127.0.0.1:8765", auto_start_local_tts=True)
        # First call is False, subsequent calls are True
        poll_results = [False, False, True]

        def mock_ready(_url):
            return poll_results.pop(0) if poll_results else True

        fake_proc = MagicMock(spec=subprocess.Popen)
        fake_proc.poll.return_value = None

        with patch.object(pm, "is_api_ready", side_effect=mock_ready), \
             patch.object(pm, "find_executable", return_value=Path("Local_TTS.exe")), \
             patch.object(pm, "start", return_value=fake_proc) as mock_start:
            pm.owned_process = fake_proc
            ready, status = pm.ensure_running(settings)
            self.assertTrue(ready)
            self.assertEqual(status, "STARTED")
            mock_start.assert_called_once()

    def test_c_api_down_executable_missing(self):
        pm = LocalTTSProcessManager()
        settings = LocalTTSSettings(base_url="http://127.0.0.1:8765", auto_start_local_tts=True)
        with patch.object(pm, "is_api_ready", return_value=False), \
             patch.object(pm, "find_executable", return_value=None):
            with self.assertRaises(FileNotFoundError):
                pm.ensure_running(settings)

    def test_d_process_exits_before_ready(self):
        pm = LocalTTSProcessManager()
        fake_proc = MagicMock(spec=subprocess.Popen)
        fake_proc.poll.return_value = 1
        fake_proc.returncode = 1
        pm.owned_process = fake_proc

        with patch.object(pm, "is_api_ready", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "LOCAL_TTS_PROCESS_EXITED"):
                pm.wait_until_ready("http://127.0.0.1:8765", timeout_seconds=2.0)

    def test_e_startup_timeout(self):
        pm = LocalTTSProcessManager()
        fake_proc = MagicMock(spec=subprocess.Popen)
        fake_proc.poll.return_value = None
        pm.owned_process = fake_proc

        with patch.object(pm, "is_api_ready", return_value=False):
            with self.assertRaises(TimeoutError):
                pm.wait_until_ready("http://127.0.0.1:8765", timeout_seconds=0.6)

    def test_f_cartoon_sub_owns_process_shutdown(self):
        pm = LocalTTSProcessManager()
        fake_proc = MagicMock(spec=subprocess.Popen)
        fake_proc.poll.return_value = None
        pm.owned_process = fake_proc

        pm.shutdown_owned_process()
        fake_proc.terminate.assert_called_once()
        self.assertIsNone(pm.owned_process)

    def test_g_external_preexisting_server_not_killed(self):
        pm = LocalTTSProcessManager()
        pm.owned_process = None  # external server

        # Calling shutdown must not throw and must do nothing
        pm.shutdown_owned_process()
        self.assertIsNone(pm.owned_process)

    def test_h_mainwindow_startup_no_deleted_qlineedit(self):
        import gc
        window = MainWindow()
        gc.collect()
        window.show()
        self.app.processEvents()

        # Verify AudioPage and QLineEdit widgets are alive and responsive
        audio_page = window.pages[5]
        self.assertIsNotNone(audio_page.tts_url)
        self.assertFalse(audio_page.tts_url.hasFocus())
        self.assertEqual(audio_page.tts_url.text(), "http://127.0.0.1:8765")
        self.assertIsNotNone(audio_page.add_path_edit)
        self.assertEqual(audio_page.add_path_edit.text(), "")

        # Verify tabs exist
        self.assertEqual(window.tabs.count(), 7)
        self.assertEqual(window.tabs.tabText(5), "Audio")
        self.assertEqual(window.tabs.tabText(6), "Export")
        window.close()


if __name__ == "__main__":
    unittest.main()
