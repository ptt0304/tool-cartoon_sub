import json
from PySide6.QtWidgets import QMainWindow, QTabWidget, QFileDialog, QMessageBox, QTableWidgetItem, QProgressBar, QPushButton
from cartoon_sub.app.controller import Controller
from cartoon_sub.ui.worker import Worker
from cartoon_sub.ui.settings_dialog import SettingsDialog
from cartoon_sub.ui.context_dialog import ContextDialog
from cartoon_sub.translation.context_service import source_fingerprint
from cartoon_sub.translation.qc import review_translation
from cartoon_sub.ui.tabs import video_tab, transcript_tab, translate_tab, subtitle_tab, mask_style_tab, export_tab

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.controller = Controller()
        self.worker = None
        self.setWindowTitle("Cartoon Sub — Phase 3 / Biên dịch Trung–Việt")
        self.resize(1100, 750)
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)
        self.pages = [module.build() for module in (video_tab, transcript_tab, translate_tab, subtitle_tab, mask_style_tab, export_tab)]
        for name, widget in zip(("Video", "Transcript", "Translate", "Subtitle", "Mask & Style", "Export"), self.pages):
            self.tabs.addTab(widget, name)
        menu = self.menuBar().addMenu("Project")
        self.open_action = menu.addAction("Open project", self.load_project)
        self.save_action = menu.addAction("Save project", self.save_project)
        self.save_action.setShortcut("Ctrl+S")
        self.menuBar().addMenu("Settings").addAction("AI…", self.open_settings)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.hide()
        self.cancel_button.clicked.connect(lambda: self.worker.cancel() if self.worker else None)
        self.statusBar().addPermanentWidget(self.progress)
        self.statusBar().addPermanentWidget(self.cancel_button)
        self.pages[0].open_button.clicked.connect(self.open_video)
        self.pages[1].import_button.clicked.connect(self.import_srt)
        self.pages[1].transcribe_button.clicked.connect(self.transcribe)
        self.pages[2].translate_button.clicked.connect(self.translate)
        self.pages[2].analyze_button.clicked.connect(self.analyze_context)
        self.pages[2].context_button.clicked.connect(lambda: self.edit_context(False))
        self.pages[2].proposal_button.clicked.connect(lambda: self.edit_context(True))
        self.refresh()

    def open_settings(self):
        try:
            SettingsDialog(self.controller, self).exec()
            if self.controller.project:
                self.sync_options()
                self.controller.save()
                self.refresh()
        except Exception as exc:
            self.error(exc)

    def error(self, message):
        # A pipeline failure persists status while keeping the previous subtitle list.
        if self.controller.directory and self.worker is not None:
            try:
                self.controller.accept(self.controller.load(self.controller.directory / "project.json"))
                self.refresh()
            except (OSError, ValueError, TypeError):
                pass
        QMessageBox.critical(self, "Cartoon Sub", str(message))

    def start_job(self, operation, accept):
        self.tabs.setEnabled(False)
        self.menuBar().setEnabled(False)
        self.progress.show()
        self.cancel_button.show()
        self.worker = Worker(operation, self)
        self.worker.result.connect(accept)
        self.worker.error.connect(self.error)
        self.worker.progress.connect(self.statusBar().showMessage)
        self.worker.finished.connect(self.job_finished)
        self.worker.start()

    def job_finished(self):
        if self.worker.cancel_event.is_set() and self.controller.directory:
            try:
                self.controller.accept(self.controller.load(self.controller.directory / "project.json"))
                self.refresh()
            except (OSError, ValueError, TypeError):
                pass
        self.tabs.setEnabled(True)
        self.menuBar().setEnabled(True)
        self.progress.hide()
        self.cancel_button.hide()
        self.worker.deleteLater()
        self.worker = None

    def sync_options(self):
        if not self.controller.project:
            return
        page = self.pages[2]
        self.controller.update_translation_options(page.preset.currentData(), page.prompt.toPlainText(),
            page.glossary.toPlainText(), [key for key, check in page.genres.items() if check.isChecked()])

    def save_project(self):
        try:
            self.sync_options()
            self.controller.save()
            self.statusBar().showMessage("Project saved", 5000)
            return True
        except Exception as exc:
            self.error(exc)
            return False

    def open_video(self):
        if not self.save_project():
            return
        video, _ = QFileDialog.getOpenFileName(self, "Chọn video", "", "Video (*.mp4 *.mkv *.mov)")
        if not video:
            return
        directory = QFileDialog.getExistingDirectory(self, "Chọn thư mục project mới (không chứa project.json)")
        if directory:
            self.start_job(lambda **job: self.controller.create(video, directory, **job), self.accept_project)

    def load_project(self):
        if not self.save_project():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Open project", "", "Project (project.json)")
        if path:
            try:
                self.accept_project(self.controller.load(path))
            except Exception as exc:
                self.error(exc)

    def accept_project(self, result):
        self.controller.accept(result)
        self.refresh()
        from pathlib import Path
        if not Path(self.controller.project.source_video_path).is_file():
            QMessageBox.warning(self, "Missing source", "Video gốc không còn ở đường dẫn đã lưu; dữ liệu subtitle vẫn mở được.")

    def import_srt(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import Chinese SRT", "", "Subtitles (*.srt)")
        if path:
            try:
                self.sync_options()
                self.controller.import_subtitles(path)
                self.refresh()
            except Exception as exc:
                self.error(exc)

    def transcribe(self):
        try:
            self.sync_options()
            self.controller.save()
            self.start_job(self.controller.transcribe, self.accept_project)
        except Exception as exc:
            self.error(exc)

    def translate(self):
        try:
            self.sync_options()
            self.controller.save()
            self.start_job(self.controller.translate, self.accept_project)
        except Exception as exc:
            self.error(exc)

    def analyze_context(self):
        try:
            self.sync_options()
            self.controller.save()
            self.start_job(self.controller.analyze_context, self.accept_project)
        except Exception as exc:
            self.error(exc)

    def edit_context(self, proposal):
        try:
            self.sync_options()
            project = self.controller.project
            if proposal and project.context_proposal_hash != source_fingerprint(project):
                raise ValueError("Transcript đã đổi; hãy phân tích lại ngữ cảnh")
            context = project.context_proposal if proposal else project.story_context
            dialog = ContextDialog(context, {s.id for s in project.segments}, self, proposal)
            if dialog.exec() == dialog.DialogCode.Accepted:
                self.controller.apply_context(dialog.result_context)
                self.refresh()
        except Exception as exc:
            self.error(exc)

    def refresh(self):
        project = self.controller.project
        for index in range(1, 6):
            self.tabs.setTabEnabled(index, project is not None)
        self.save_action.setEnabled(project is not None)
        if project is None:
            return
        self.pages[0].metadata.setPlainText(project.source_video_path + "\n\n" + json.dumps(project.metadata, ensure_ascii=False, indent=2))
        page = self.pages[2]
        page.preset.setCurrentIndex(max(0, page.preset.findData(project.translation_preset)))
        page.prompt.setPlainText(project.translation_prompt)
        page.glossary.setPlainText("\n".join(f"{k} -> {v}" for k, v in project.glossary.items()))
        for key, check in page.genres.items():
            check.setChecked(key in project.translation_genres)
        ready = bool(project.segments) and project.context_source_hash == source_fingerprint(project)
        page.translate_button.setEnabled(ready)
        page.analyze_button.setEnabled(bool(project.segments))
        page.context_button.setEnabled(bool(project.segments))
        page.proposal_button.setEnabled(bool(project.context_proposal))
        context = project.story_context
        status = "Hồ sơ đã áp dụng cho transcript này" if ready else "Cần duyệt đề xuất hoặc tự nhập và áp dụng hồ sơ"
        if project.context_status == "proposal_ready":
            status += " • Có đề xuất AI mới đang chờ duyệt"
        states = {"completed": "Hoàn tất", "not_started": "Chưa dịch", "stale": "Cần dịch cập nhật — cấu hình/nguồn đã đổi",
                  "running": "Đang dịch", "failed": "Lỗi — bấm tiếp tục", "cancelled": "Đã hủy — có thể tiếp tục"}
        page.summary.setText(f"{status}\nNhân vật: {len(context.get('characters', []))} • Thuật ngữ: {len(context.get('terms', []))} "
            f"• Quy tắc xưng hô: {len(context.get('address_rules', []))} • Nghi vấn ngữ cảnh: {len(context.get('uncertainties', []))}\n"
            f"Bản dịch: {states.get(project.translation_status, project.translation_status)}")
        model = self.controller.settings_store.load().translation_model
        page.summary.setText(page.summary.text() + f"\nModel dịch/ngữ cảnh: {model}" +
            (" — model 2.5 có thể bị hạn chế; đổi trong Settings > AI." if "gemini-2.5-" in model else ""))
        self.pages[1].transcribe_button.setEnabled(project.transcription_status != "imported")
        warnings = review_translation(project)
        for table, bilingual in ((self.pages[1].table, False), (self.pages[3].table, True)):
            table.setRowCount(len(project.segments))
            for row, s in enumerate(project.segments):
                values = [s.id, s.zh, s.vi, f"{s.duration:.3f}", " | ".join(warnings[str(s.id)]) or "Không có cảnh báo tự động"] if bilingual else [s.id, f"{s.start:.3f}", f"{s.end:.3f}", s.zh]
                for col, value in enumerate(values):
                    item = QTableWidgetItem(str(value))
                    item.setToolTip(str(value))
                    table.setItem(row, col, item)
        self.statusBar().showMessage(f"{project.name}: {len(project.segments)} subtitles — {project.transcription_status}")

    def closeEvent(self, event):
        if self.worker is not None:
            self.worker.cancel()
            self.statusBar().showMessage("Đang hủy job; đóng lại sau khi hoàn tất.")
            event.ignore()
        elif self.save_project():
            event.accept()
        else:
            event.ignore()
