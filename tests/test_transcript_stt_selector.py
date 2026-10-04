import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QComboBox, QLineEdit

from cartoon_sub.app.settings import AISettings, SettingsStore
from cartoon_sub.transcription.pipeline import resolve_transcript_stt_settings
from cartoon_sub.ui.model_search import SearchableModelBinding
from cartoon_sub.ui.tabs.transcript_tab import build
from cartoon_sub.ui.tabs import translate_tab, subtitle_tab, audio_tab


MODELS = [
    {"id": "openai/whisper-a", "name": "Whisper Alpha"},
    {"id": "google/transcribe-b", "name": "Speech Beta"},
]


class TranscriptSTTSelectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_transcript_has_separate_stt_and_general_selectors(self):
        tab = build()
        self.assertIsNot(tab.stt_model, tab.ai_model)
        self.assertIsNot(tab.stt_model_search, tab.ai_model_search)
        tab.close()

    def test_other_ai_tabs_expose_searchable_general_selector(self):
        tabs = [translate_tab.build(), subtitle_tab.build(), audio_tab.build()]
        try:
            for tab in tabs:
                self.assertIsInstance(tab.ai_model_search, QLineEdit)
                self.assertIsInstance(tab.ai_model, QComboBox)
        finally:
            for tab in tabs:
                tab.close()

    def test_search_matches_provider_name_and_id_without_losing_selection(self):
        search, combo = QLineEdit(), QComboBox()
        binding = SearchableModelBinding(search, combo)
        binding.set_models(MODELS, selected_id="google/transcribe-b",
                           default_label="Mặc định — openai/default")
        search.setText("openai")
        self.assertEqual(combo.currentData(), "google/transcribe-b")
        self.assertIn("Ẩn bởi tìm kiếm", combo.currentText())
        search.clear()
        self.assertEqual(combo.currentData(), "google/transcribe-b")
        search.setText("speech beta")
        self.assertEqual(combo.findData("google/transcribe-b") >= 0, True)

    def test_override_precedes_settings_and_is_openrouter(self):
        settings = AISettings(transcription_provider="gemini",
                              transcription_model="legacy-model",
                              tab_model_overrides={"transcript_stt": "openai/whisper-a"})
        resolved = resolve_transcript_stt_settings(settings)
        self.assertEqual(resolved.transcription_model, "openai/whisper-a")
        self.assertEqual(resolved.transcription_provider, "openrouter")

    def test_default_and_missing_model_behavior(self):
        settings = AISettings(transcription_provider="openrouter",
                              transcription_model="google/transcribe-b")
        self.assertIs(resolve_transcript_stt_settings(settings), settings)
        settings.transcription_model = ""
        with self.assertRaisesRegex(ValueError, "Chưa chọn model STT"):
            resolve_transcript_stt_settings(settings)

    def test_explicit_stt_override_persists_and_default_is_not_duplicated(self):
        with tempfile.TemporaryDirectory() as root:
            store = SettingsStore(Path(root), SimpleNamespace(get_password=lambda *_: None))
            store.save(AISettings(transcription_provider="openrouter",
                                  transcription_model="google/transcribe-b"))
            store.set_tab_model_override("transcript_stt", "openai/whisper-a")
            self.assertEqual(store.load().tab_model_overrides["transcript_stt"],
                             "openai/whisper-a")
            store.set_tab_model_override("transcript_stt", "")
            self.assertNotIn("transcript_stt", store.load().tab_model_overrides)


if __name__ == "__main__":
    unittest.main()
