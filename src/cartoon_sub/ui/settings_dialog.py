from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QLineEdit,
    QComboBox, QSpinBox, QPushButton, QDialogButtonBox, QMessageBox, QFileDialog, QWidget)
from cartoon_sub.app.settings import AISettings
from cartoon_sub.ai.text_client import PROVIDERS, provider_label, provider_models
from cartoon_sub.ui.worker import Worker


class SettingsDialog(QDialog):
    def __init__(self, controller, parent=None):
        super().__init__(parent);self.controller=controller;self.worker=None
        self.setWindowTitle("Settings > AI");self.resize(680,560);settings=controller.settings_store.load();layout=QVBoxLayout(self)
        link=QLabel('Gemini key: <a href="https://aistudio.google.com/apikey">Google AI Studio</a>. Các provider khác lấy key tại trang quản lý của provider.')
        link.setOpenExternalLinks(True);link.setWordWrap(True);layout.addWidget(link)
        self.status=QLabel(controller.settings_store.key_status());self.status.setWordWrap(True);self.status.setTextFormat(Qt.TextFormat.PlainText);layout.addWidget(self.status)
        form=QFormLayout();self.key_file=QLineEdit(settings.gemini_api_keys_file);self.key_file.setPlaceholderText("Mỗi dòng một Gemini API key")
        key_row=QWidget();key_layout=QHBoxLayout(key_row);key_layout.setContentsMargins(0,0,0,0)
        key_layout.addWidget(self.key_file,1);self.choose_key_file=QPushButton("Chọn file")
        self.choose_key_file.clicked.connect(self.select_key_file);key_layout.addWidget(self.choose_key_file)
        form.addRow("File Gemini API keys",key_row)
        self.key_file.textChanged.connect(self.update_key_file_status)
        self.provider=QComboBox();self.provider.addItem("Google Gemini","gemini")
        for key in PROVIDERS:self.provider.addItem(provider_label(key),key)
        self.provider.setCurrentIndex(max(0,self.provider.findData(settings.translation_provider)));form.addRow("Provider dịch/ngữ cảnh",self.provider)
        self.translation_key=QLineEdit();self.translation_key.setEchoMode(QLineEdit.EchoMode.Password);self.translation_key.setPlaceholderText("Để trống để giữ key của provider đã lưu")
        form.addRow("Translation API key",self.translation_key)
        self.transcription_model=QComboBox();self.translation_model=QComboBox()
        for control,value in ((self.transcription_model,settings.transcription_model),(self.translation_model,settings.translation_model)):
            control.setEditable(True);control.addItems(["gemini-3.5-flash","gemini-3.1-flash-lite","gemini-2.5-flash"]);control.setCurrentText(value)
        form.addRow("Transcription model (Gemini)",self.transcription_model);form.addRow("Translation/context model",self.translation_model)
        self.chunk=QSpinBox();self.chunk.setRange(30,50);self.chunk.setValue(settings.translation_chunk_size);form.addRow("Translation chunk size",self.chunk)
        self.retry=QSpinBox();self.retry.setRange(0,5);self.retry.setValue(settings.retry_count);form.addRow("Retry count",self.retry);layout.addLayout(form)
        note=QLabel("Audio transcription và căn timing audio chỉ dùng Gemini. Dịch, phân tích ngữ cảnh, dubbing text và semantic fallback dùng provider dịch đã chọn. Test Gemini chỉ đọc metadata; Test provider dịch gửi một request text nhỏ và có thể dùng quota.")
        note.setWordWrap(True);layout.addWidget(note)
        row=QVBoxLayout();self.test=QPushButton("Test Gemini transcription");self.test_translation=QPushButton("Test provider dịch/ngữ cảnh")
        self.test.clicked.connect(self.test_connection);self.test_translation.clicked.connect(self.test_translation_connection);row.addWidget(self.test);row.addWidget(self.test_translation);layout.addLayout(row)
        self.buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel);self.buttons.accepted.connect(self.save);self.buttons.rejected.connect(self.reject);layout.addWidget(self.buttons)
        self._opening=True;self.provider.currentIndexChanged.connect(self.provider_changed);self.provider_changed();self._opening=False
        if self.key_file.text().strip():self.update_key_file_status(self.key_file.text())

    def provider_changed(self,*args):
        provider=self.provider.currentData()
        current=self.translation_model.currentText();models=provider_models(provider)
        self.translation_model.blockSignals(True);self.translation_model.clear();self.translation_model.addItems(models)
        self.translation_model.setCurrentText(current if getattr(self,"_opening",False) or current in models else models[0]);self.translation_model.blockSignals(False)
        self.translation_key.setEnabled(provider != "gemini");self.status.setText(self.controller.settings_store.key_status(provider))

    def values(self):
        return AISettings(self.transcription_model.currentText().strip(),self.translation_model.currentText().strip(),
            self.chunk.value(),self.retry.value(),self.provider.currentData(),self.key_file.text()).validate()

    def select_key_file(self):
        path,_=QFileDialog.getOpenFileName(self,"Chọn file Gemini API keys",self.key_file.text(),"Text files (*.txt);;All files (*.*)")
        if path:self.key_file.setText(path)

    def update_key_file_status(self, path):
        if not path.strip():
            self.status.setText(self.controller.settings_store.key_status())
            return
        try:
            from cartoon_sub.ai.gemini_client import load_gemini_keys
            count=len(load_gemini_keys(path));self.status.setText(f"Đã tải {count} Gemini API keys")
        except (OSError, ValueError) as exc:self.status.setText(str(exc))

    def save(self):
        try:self.controller.settings_store.save(self.values(),"",self.translation_key.text());self.translation_key.clear();self.accept()
        except Exception as exc:QMessageBox.critical(self,"Settings",str(exc))

    def start_test(self, callback):
        try:
            self.worker=Worker(callback,self);self.worker.progress.connect(self.status.setText);self.worker.result.connect(self.status.setText);self.worker.error.connect(self.status.setText);self.worker.finished.connect(self.finished_test)
            self.test.setEnabled(False);self.test_translation.setEnabled(False);self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(False);self.worker.start()
        except Exception as exc:self.status.setText(str(exc))

    def test_connection(self):
        settings=self.values();self.start_test(lambda **job:self.controller.test_connection(settings,**job))

    def test_translation_connection(self):
        settings,key=self.values(),self.translation_key.text();self.start_test(lambda **job:self.controller.test_translation_connection(settings,key,**job))

    def finished_test(self):
        self.worker.deleteLater();self.worker=None;self.test.setEnabled(True);self.test_translation.setEnabled(True);self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(True)

    def reject(self):
        if self.worker is not None:self.worker.cancel();self.status.setText("Đang hủy kiểm tra; chờ request hiện tại kết thúc.")
        else:self.translation_key.clear();super().reject()

    def closeEvent(self,event):
        if self.worker is not None:self.reject();event.ignore()
        else:super().closeEvent(event)
