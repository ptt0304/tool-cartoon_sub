from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QLabel, QLineEdit,
    QComboBox, QSpinBox, QPushButton, QDialogButtonBox, QMessageBox)
from cartoon_sub.app.settings import AISettings
from cartoon_sub.ui.worker import Worker


class SettingsDialog(QDialog):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.worker = None
        self.setWindowTitle("Settings > AI — Gemini")
        self.resize(640, 440)
        settings = controller.settings_store.load()
        layout = QVBoxLayout(self)
        link = QLabel('Lấy key tại <a href="https://aistudio.google.com/apikey">Google AI Studio → API keys</a>.')
        link.setOpenExternalLinks(True)
        layout.addWidget(link)
        self.status = QLabel(controller.settings_store.key_status())
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        form = QFormLayout()
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("Nhập key mới; để trống để giữ key đã lưu")
        form.addRow("Gemini API key", self.key)
        self.transcription_model = QComboBox()
        self.translation_model = QComboBox()
        for control, value in ((self.transcription_model, settings.transcription_model),
                               (self.translation_model, settings.translation_model)):
            control.setEditable(True)
            control.addItems(["gemini-3.5-flash", "gemini-3.1-flash-lite", "gemini-2.5-flash"])
            control.setCurrentText(value)
        form.addRow("Transcription model", self.transcription_model)
        form.addRow("Translation / context model", self.translation_model)
        self.chunk = QSpinBox()
        self.chunk.setRange(30, 50)
        self.chunk.setValue(settings.translation_chunk_size)
        form.addRow("Translation chunk size", self.chunk)
        self.retry = QSpinBox()
        self.retry.setRange(0, 5)
        self.retry.setValue(settings.retry_count)
        form.addRow("Retry count", self.retry)
        layout.addLayout(form)
        legacy_note = QLabel("Project mới nên chọn gemini-3.5-flash. Google hạn chế quyền dùng model 2.5 "
                             "cho project mới; model 2.5 có thể trả 404 dù Test Connection đọc được metadata. "
                             "Cấu hình đã lưu không tự thay đổi: chọn model rồi bấm Save.")
        legacy_note.setWordWrap(True)
        layout.addWidget(legacy_note)
        note = QLabel("Key lưu trong OS keyring, không lưu vào project. Test Connection chỉ đọc thông tin model, "
                      "không gửi audio. Khi chạy transcription, tool gửi từng đoạn audio tới Google Gemini. "
                      "Cancel sẽ chờ request đang gửi kết thúc hoặc timeout (120 giây).")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.test = QPushButton("Test Connection")
        self.test.clicked.connect(self.test_connection)
        layout.addWidget(self.test)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.save)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def values(self):
        return AISettings(self.transcription_model.currentText().strip(), self.translation_model.currentText().strip(),
                          self.chunk.value(), self.retry.value()).validate()

    def save(self):
        try:
            self.controller.settings_store.save(self.values(), self.key.text())
            self.key.clear()
            self.accept()
        except Exception as exc:
            QMessageBox.critical(self, "Settings", str(exc))

    def test_connection(self):
        try:
            settings, key = self.values(), self.key.text()
            self.worker = Worker(lambda **job: self.controller.test_connection(settings, key, **job), self)
            self.worker.progress.connect(self.status.setText)
            self.worker.result.connect(self.status.setText)
            self.worker.error.connect(self.status.setText)
            self.worker.finished.connect(self.finished_test)
            self.test.setEnabled(False)
            self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(False)
            self.worker.start()
        except Exception as exc:
            self.status.setText(str(exc))

    def finished_test(self):
        self.worker.deleteLater()
        self.worker = None
        self.test.setEnabled(True)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(True)

    def reject(self):
        if self.worker is not None:
            self.worker.cancel()
            self.status.setText("Đang hủy kiểm tra; chờ request hiện tại kết thúc rồi đóng lại.")
        else:
            self.key.clear()
            super().reject()

    def closeEvent(self, event):
        if self.worker is not None:
            self.reject()
            event.ignore()
        else:
            super().closeEvent(event)
