from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QLineEdit,
    QComboBox, QSpinBox, QPushButton, QDialogButtonBox, QMessageBox, QFileDialog, QWidget)

from cartoon_sub.app.settings import AISettings, load_api_keys
from cartoon_sub.ai.text_client import PROVIDER_CATALOG, provider_label, provider_models
from cartoon_sub.ui.worker import Worker


class SettingsDialog(QDialog):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller, self.worker = controller, None
        self.saved_settings = controller.settings_store.load()
        self._last_choices = {
            "transcription": (self.saved_settings.transcription_provider, self.saved_settings.transcription_model),
            "text": (self.saved_settings.translation_provider, self.saved_settings.translation_model),
        }
        self.enabled_providers = []
        self.setWindowTitle("Settings > AI"); self.resize(680, 560)
        layout = QVBoxLayout(self)
        link = QLabel('API keys: <a href="https://aistudio.google.com/apikey">Google AI Studio</a>. '
                      'Các provider khác lấy key tại trang quản lý tương ứng.')
        link.setOpenExternalLinks(True); link.setWordWrap(True); layout.addWidget(link)
        self.status = QLabel(); self.status.setWordWrap(True); self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)

        form = QFormLayout()
        default_path = self.saved_settings.api_key_file or str(Path.cwd() / "api_key.txt")
        self.key_file = QLineEdit(default_path)
        key_row = QWidget(); key_layout = QHBoxLayout(key_row); key_layout.setContentsMargins(0, 0, 0, 0)
        key_layout.addWidget(self.key_file, 1); self.choose_key_file = QPushButton("Chọn file")
        self.choose_key_file.clicked.connect(self.select_key_file); key_layout.addWidget(self.choose_key_file)
        form.addRow("File API keys", key_row)
        self.provider = QComboBox(); form.addRow("Provider", self.provider)
        self.transcription_model = QComboBox(); form.addRow("Transcription model", self.transcription_model)
        self.translation_model = QComboBox(); form.addRow("Translation/context model", self.translation_model)
        self.chunk = QSpinBox(); self.chunk.setRange(30, 50); self.chunk.setValue(self.saved_settings.translation_chunk_size)
        form.addRow("Translation chunk size", self.chunk)
        self.retry = QSpinBox(); self.retry.setRange(0, 5); self.retry.setValue(self.saved_settings.retry_count)
        form.addRow("Retry count", self.retry); layout.addLayout(form)

        note = QLabel("Danh sách chỉ gồm provider có key. Transcription chỉ hiện model có capability audio; "
                      "translation/context chỉ hiện model text.")
        note.setWordWrap(True); layout.addWidget(note)
        row = QVBoxLayout(); self.test = QPushButton("Test transcription model")
        self.test_translation = QPushButton("Test translation/context model")
        self.test.clicked.connect(self.test_connection)
        self.test_translation.clicked.connect(self.test_translation_connection)
        row.addWidget(self.test); row.addWidget(self.test_translation); layout.addLayout(row)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.save); self.buttons.rejected.connect(self.reject); layout.addWidget(self.buttons)

        self.provider.currentIndexChanged.connect(self.provider_changed)
        self.key_file.textChanged.connect(self.reload_api_file)
        self.reload_api_file(initial=True)

    def select_key_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Chọn file API keys", self.key_file.text(),
                                              "Text files (*.txt);;All files (*.*)")
        if path:
            self.key_file.setText(path)

    def reload_api_file(self, *_args, initial=False):
        preferred_filter = self.saved_settings.provider_filter if initial else self.provider.currentData()
        try:
            keys = load_api_keys(self.key_file.text())
            self.enabled_providers = [provider for provider in PROVIDER_CATALOG if keys.get(provider)]
            names = ", ".join(provider_label(provider) for provider in self.enabled_providers)
            self.status.setText(f"API providers khả dụng: {len(self.enabled_providers)}/{len(PROVIDER_CATALOG)}" +
                                (f" — {names}" if names else "\nKhông có AI provider nào được cấu hình API key."))
        except ValueError as exc:
            self.enabled_providers = []
            self.status.setText(str(exc))
        self.provider.blockSignals(True); self.provider.clear(); self.provider.addItem("Tất cả", "all")
        for provider in self.enabled_providers:
            self.provider.addItem(provider_label(provider), provider)
        index = self.provider.findData(preferred_filter)
        self.provider.setCurrentIndex(index if index >= 0 else 0); self.provider.blockSignals(False)
        self.provider_changed(initial=initial)

    @staticmethod
    def _selected(control):
        value = control.currentData()
        return tuple(value) if isinstance(value, (tuple, list)) and len(value) == 2 else None

    @staticmethod
    def _find_model(control, preferred):
        if not preferred:
            return -1
        preferred = tuple(preferred)
        for index in range(control.count()):
            value = control.itemData(index)
            if isinstance(value, (tuple, list)) and tuple(value) == preferred:
                return index
        return -1

    def _populate_models(self, control, capability, preferred):
        if preferred:
            self._last_choices[capability] = tuple(preferred)
        selected_filter = self.provider.currentData()
        providers = self.enabled_providers if selected_filter == "all" else [selected_filter]
        options = [(provider, model) for provider in providers
                   for model in provider_models(provider, capability)]
        control.blockSignals(True); control.clear()
        for provider, model in options:
            display = f"{provider_label(provider)} — {model}" if selected_filter == "all" else model
            control.addItem(display, (provider, model))
        index = self._find_model(control, preferred)
        if index < 0 and preferred:
            index = next((i for i, value in enumerate(options) if value[0] == preferred[0]), -1)
        if options:
            control.setCurrentIndex(index if index >= 0 else 0)
            selected = self._selected(control)
            if selected:
                self._last_choices[capability] = selected
        else:
            control.addItem("Không có model hỗ trợ audio/transcription" if capability == "transcription"
                            else "Không có model hỗ trợ translation/context", None)
            control.setCurrentIndex(0)
        control.setEnabled(bool(options)); control.blockSignals(False)

    def provider_changed(self, *_args, initial=False):
        old_transcription = ((self.saved_settings.transcription_provider, self.saved_settings.transcription_model)
                             if initial else self._selected(self.transcription_model))
        old_translation = ((self.saved_settings.translation_provider, self.saved_settings.translation_model)
                           if initial else self._selected(self.translation_model))
        self._populate_models(self.transcription_model, "transcription", old_transcription)
        self._populate_models(self.translation_model, "text", old_translation)
        if self.worker is None:
            self.test.setEnabled(self._selected(self.transcription_model) is not None)
            self.test_translation.setEnabled(self._selected(self.translation_model) is not None)

    def _selection_or_fallback(self, control, capability):
        selected = self._selected(control)
        if selected:
            return selected
        preferred = self._last_choices[capability]
        if preferred[0] in self.enabled_providers and preferred[1] in provider_models(preferred[0], capability):
            return preferred
        return next(((provider, models[0]) for provider in self.enabled_providers
                     if (models := provider_models(provider, capability))), None)

    def values(self):
        transcription = self._selection_or_fallback(self.transcription_model, "transcription")
        translation = self._selection_or_fallback(self.translation_model, "text")
        if not transcription:
            raise ValueError("Provider đang chọn không có model hỗ trợ transcription audio.")
        if not translation:
            raise ValueError("Provider đang chọn không có model hỗ trợ translation/context.")
        return AISettings(transcription_model=transcription[1], translation_model=translation[1],
            translation_chunk_size=self.chunk.value(), retry_count=self.retry.value(),
            translation_provider=translation[0], api_key_file=self.key_file.text(),
            provider_filter=self.provider.currentData(), transcription_provider=transcription[0]).validate()

    def save(self):
        try:
            self.controller.settings_store.save(self.values()); self.accept()
        except Exception as exc:
            QMessageBox.critical(self, "Settings", str(exc))

    def start_test(self, callback):
        try:
            self.worker = Worker(callback, self); self.worker.progress.connect(self.status.setText)
            self.worker.result.connect(self.status.setText); self.worker.error.connect(self.status.setText)
            self.worker.finished.connect(self.finished_test)
            self.test.setEnabled(False); self.test_translation.setEnabled(False)
            self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(False); self.worker.start()
        except Exception as exc:
            self.status.setText(str(exc))

    def test_connection(self):
        try:
            settings = self.values()
        except ValueError as exc:
            self.status.setText(str(exc)); return
        self.start_test(lambda **job: self.controller.test_connection(settings, **job))

    def test_translation_connection(self):
        try:
            settings = self.values()
        except ValueError as exc:
            self.status.setText(str(exc)); return
        self.start_test(lambda **job: self.controller.test_translation_connection(settings, **job))

    def finished_test(self):
        self.worker.deleteLater(); self.worker = None
        self.test.setEnabled(self._selected(self.transcription_model) is not None)
        self.test_translation.setEnabled(self._selected(self.translation_model) is not None)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(True)

    def reject(self):
        if self.worker is not None:
            self.worker.cancel(); self.status.setText("Đang hủy kiểm tra; chờ request hiện tại kết thúc.")
        else:
            super().reject()

    def closeEvent(self, event):
        if self.worker is not None:
            self.reject(); event.ignore()
        else:
            super().closeEvent(event)
