import json
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMainWindow, QTabWidget, QFileDialog, QMessageBox, QTableWidgetItem, QProgressBar, QPushButton, QInputDialog, QLabel
from cartoon_sub.app.controller import Controller
from cartoon_sub.ui.worker import Worker
from cartoon_sub.ui.settings_dialog import SettingsDialog
from cartoon_sub.ui.context_dialog import ContextDialog
from cartoon_sub.translation.context_service import source_fingerprint
from cartoon_sub.speaker.service import review_complete, refresh_timeline
from cartoon_sub.ui.speaker_dialog import SpeakerDialog
from cartoon_sub.ui.dubbing_settings_dialog import DubbingSettingsDialog
from cartoon_sub.ui.utterance_dialog import UtteranceDialog
from cartoon_sub.ui.docs_dialog import DocsWindow
from cartoon_sub.ui.timeline_table import populate, selected_ids
from cartoon_sub.syllable.target import DubbingSettings
from cartoon_sub.ui.tabs import video_tab, transcript_tab, translate_tab, subtitle_tab, mask_style_tab, audio_tab, export_tab

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.controller = Controller()
        self.worker = None
        self.local_tts_voices = None
        self.setWindowTitle("Cartoon Sub — Master Timeline / Speaker & Dubbing")
        self.resize(1100, 750)
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)
        self.pages = [module.build() for module in (video_tab, transcript_tab, translate_tab, subtitle_tab, mask_style_tab, audio_tab, export_tab)]
        for name, widget in zip(("Video", "Transcript", "Translate", "Subtitle", "Mask & Style", "Audio", "Export"), self.pages):
            self.tabs.addTab(widget, name)
        menu = self.menuBar().addMenu("Project")
        self.open_action = menu.addAction("Open project", self.load_project)
        self.save_action = menu.addAction("Save project", self.save_project)
        self.save_action.setShortcut("Ctrl+S")
        self.menuBar().addMenu("Settings").addAction("AI…", self.open_settings)
        self.menuBar().actions()[-1].menu().addAction("Translation / Dubbing…", self.open_dubbing_settings)
        self.docs_button = QPushButton("Docs")
        self.docs_button.clicked.connect(self.show_docs)
        self.menuBar().setCornerWidget(self.docs_button, Qt.Corner.TopRightCorner)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.hide()
        self.cancel_button.clicked.connect(lambda: self.worker.cancel() if self.worker else None)
        self.copyright_label = QLabel("© PHẠM THANH TÙNG - 0866891380")
        self.statusBar().addPermanentWidget(self.copyright_label)
        self.statusBar().addPermanentWidget(self.progress)
        self.statusBar().addPermanentWidget(self.cancel_button)
        self.pages[0].open_button.clicked.connect(self.open_video)
        self.pages[1].import_button.clicked.connect(self.import_srt)
        self.pages[1].transcribe_button.clicked.connect(self.transcribe)
        self.pages[1].speaker_button.clicked.connect(self.edit_speakers)
        self.pages[2].translate_button.clicked.connect(self.translate)
        self.pages[2].analyze_button.clicked.connect(self.analyze_context)
        self.pages[2].context_button.clicked.connect(lambda: self.edit_context(False))
        self.pages[2].proposal_button.clicked.connect(lambda: self.edit_context(True))
        self.pages[2].view.currentIndexChanged.connect(self.refresh_timeline_table)
        self.pages[2].edit_button.clicked.connect(self.edit_utterance)
        self.pages[2].optimize_button.clicked.connect(self.optimize_dubbing)
        subtitle = self.pages[3]
        subtitle.apply_settings.clicked.connect(self.apply_segmentation_settings)
        subtitle.warning_filter.currentIndexChanged.connect(self.refresh_segmentation_page)
        subtitle.auto_all.clicked.connect(lambda: self.auto_segment(False))
        subtitle.auto_selected.clicked.connect(lambda: self.auto_segment(True))
        subtitle.refine_audio.clicked.connect(self.refine_display_timing)
        subtitle.split_manual.clicked.connect(self.split_display_segment)
        subtitle.merge.clicked.connect(self.merge_display_segments)
        subtitle.reset.clicked.connect(self.reset_segmentation)
        self.pages[4].frame_button.clicked.connect(self.load_mask_frame)
        self.pages[4].preview_button.clicked.connect(lambda:self.render_video(True))
        self.pages[4].render_button.clicked.connect(lambda:self.render_video(False))
        self.pages[4].save_button.clicked.connect(self.save_project)

        audio = self.pages[5]
        audio.connection_requested.connect(self.test_local_tts_connection)
        audio.retry_start_requested.connect(self.auto_start_local_tts)
        audio.select_exe_requested.connect(self.select_local_tts_executable)
        audio.preview_requested.connect(self.preview_local_tts_voice)
        audio.generate_requested.connect(self.generate_tts)
        audio.mix_dubbed_requested.connect(self.mix_tts)
        audio.mapping_changed.connect(self.update_speaker_tts_voice)
        audio.audio_settings_changed.connect(self.update_audio_settings)
        audio.clear_additional_requested.connect(self.clear_additional_audio)
        audio.mix_final_requested.connect(self.mix_final_audio)

        export = self.pages[6]
        export.export_button.clicked.connect(self.export_speakers)
        export.render_requested.connect(self.render_export_video)
        self.refresh()
        from PySide6.QtCore import QTimer
        QTimer.singleShot(150, self.auto_start_local_tts)

    def show_docs(self):
        if not hasattr(self, "docs_window") or self.docs_window is None:
            self.docs_window = DocsWindow(self)
            self.docs_window.destroyed.connect(lambda *_: setattr(self, "docs_window", None))
        self.docs_window.show()
        self.docs_window.raise_()
        self.docs_window.activateWindow()

    def load_mask_frame(self):
        from cartoon_sub.media.preview import VideoRenderer
        try:
            page=self.pages[4];page.player.stop()
            project=self.controller.project
            start=page.time.value()
            self.start_job(lambda **job:VideoRenderer().frame(project,self.controller.directory,start,**job),page.show_frame)
        except Exception as exc:self.error(exc)

    def render_video(self,preview):
        from cartoon_sub.media.preview import VideoRenderer
        from cartoon_sub.subtitle.models import Project
        try:
            if not self.save_project():return
            page=self.pages[4];page.player.stop()
            project=Project.from_dict(self.controller.project.to_dict())
            start=page.time.value()
            self.start_job(lambda **job:VideoRenderer().render(project,self.controller.directory,start,preview,**job),
                page.show_preview if preview else lambda path:page.output.setText('Render hoàn tất: '+str(path)))
        except Exception as exc:self.error(exc)

    def open_dubbing_settings(self):
        try:
            settings=DubbingSettings(**self.controller.project.dubbing_settings) if self.controller.project else self.controller.settings_store.load_dubbing()
            dialog=DubbingSettingsDialog(settings,self)
            if dialog.exec()==dialog.DialogCode.Accepted:
                values=dialog.values()
                self.controller.settings_store.save_dubbing(values)
                if self.controller.project:
                    self.sync_options()
                    self.controller.project.dubbing_settings=values.to_dict()
                    for segment in self.controller.project.segments:
                        segment.translation_mode=values.mode
                        if segment.dubbing_optimized: segment.dubbing_status="stale"
                    self.controller.save();self.refresh()
        except Exception as exc:self.error(exc)

    def edit_speakers(self):
        try:
            self.sync_options()
            dialog=SpeakerDialog(self.controller.project,self.controller.directory,self)
            if dialog.exec()==dialog.DialogCode.Accepted:
                self.controller.project=dialog.project
                self.controller.save();self.refresh()
        except Exception as exc:self.error(exc)

    def refresh_timeline_table(self):
        if self.controller.project:
            populate(self.pages[2].table,self.controller.project,self.pages[2].view.currentData())

    def refresh_segmentation_page(self):
        if self.controller.project:
            self.pages[3].populate(self.controller.project)

    def edit_utterance(self):
        try:
            ids=selected_ids(self.pages[2].table)
            if not ids:raise ValueError("Chọn một câu trong bảng")
            segment=next(s for s in self.controller.project.segments if s.id==ids[0])
            dialog=UtteranceDialog(segment,self)
            if dialog.exec()==dialog.DialogCode.Accepted:
                self.sync_options()
                self.controller.edit_utterance(segment.id,dialog.subtitle.toPlainText(),dialog.dubbing.toPlainText(),dialog.mode.currentData(),dialog.target.value())
                self.refresh()
        except Exception as exc:self.error(exc)

    def optimize_dubbing(self):
        try:
            ids=selected_ids(self.pages[2].table)
            if not ids:raise ValueError("Chọn một hoặc nhiều câu trong bảng")
            self.sync_options();self.controller.save()
            self.start_job(lambda **job:self.controller.optimize_dubbing(ids,**job),self.accept_project)
        except Exception as exc:self.error(exc)

    def apply_segmentation_settings(self):
        try:
            profile, settings = self.pages[3].values()
            self.controller.update_segmentation_settings(profile, settings)
            self.controller.save()
            self.refresh()
        except Exception as exc:self.error(exc)

    def auto_segment(self, selected):
        try:
            profile, settings = self.pages[3].values()
            self.controller.update_segmentation_settings(profile, settings)
            ids = self.pages[3].selected_utterance_ids() if selected else None
            if selected and not ids:
                raise ValueError("Chọn ít nhất một Utterance hoặc DisplaySegment")
            self.start_job(lambda **job: self.controller.auto_segment(ids, **job), self.accept_project)
        except Exception as exc:self.error(exc)

    def refine_display_timing(self):
        try:
            ids = self.pages[3].selected_utterance_ids()
            if not ids:
                raise ValueError("Chọn Utterance dài hoặc DisplaySegment cần căn theo audio")
            self.start_job(lambda **job: self.controller.refine_display_timing(ids, **job), self.accept_project)
        except Exception as exc:self.error(exc)

    def split_display_segment(self):
        try:
            selected = self.pages[3].selected_display_refs()
            if len(selected) != 1:
                raise ValueError("Chọn đúng một DisplaySegment để tách")
            word_index, accepted = QInputDialog.getInt(self, "Split Manually", "Tách sau từ thứ:", 1, 1, 1000)
            if accepted:
                self.controller.split_display_segment(*selected[0], word_index)
                self.refresh()
        except Exception as exc:self.error(exc)

    def merge_display_segments(self):
        try:
            selected = self.pages[3].selected_display_refs()
            if len(selected) < 2:
                raise ValueError("Chọn ít nhất hai DisplaySegment liền nhau để gộp")
            utterance_ids = {utterance_id for utterance_id, _ in selected}
            if len(utterance_ids) != 1:
                raise ValueError("Chỉ gộp các DisplaySegment thuộc cùng một Utterance")
            self.controller.merge_display_segments(utterance_ids.pop(), [display_id for _, display_id in selected])
            self.refresh()
        except Exception as exc:self.error(exc)

    def reset_segmentation(self):
        try:
            ids = self.pages[3].selected_utterance_ids()
            if not ids:
                raise ValueError("Chọn ít nhất một Utterance hoặc DisplaySegment để reset")
            self.controller.reset_segmentation(ids)
            self.refresh()
        except Exception as exc:self.error(exc)

    def export_speakers(self):
        try:
            self.sync_options();self.controller.save()
            text_type=self.pages[6].text_type.currentData()
            self.start_job(lambda **job:self.controller.export_speaker_files(text_type,**job),
                           lambda path:self.pages[6].path_label.setText(str(path)))
        except Exception as exc:self.error(exc)

    def test_local_tts_connection(self, url):
        try:
            self.start_job(
                lambda **job: self.controller.test_local_tts_connection(url, **job),
                self.accept_local_tts_connection,
            )
        except Exception as exc:
            self.error(exc)

    def auto_start_local_tts(self):
        page = self.pages[5]
        page.tts_connection_status.setText("Starting Local_TTS...")
        self.start_job(
            lambda **job: self.controller.ensure_local_tts_running(**job),
            self.accept_local_tts_connection,
        )

    def select_local_tts_executable(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Chọn file Local_TTS",
            "",
            "Executable (*.exe);;All Files (*)",
        )
        if path:
            self.controller.set_local_tts_executable(path)
            self.auto_start_local_tts()

    def accept_local_tts_connection(self, result):
        self.local_tts_voices = result["voices"]
        page = self.pages[5]
        page.set_connection_result(result["health"], result["voices"])
        if self.controller.project:
            page.populate(self.controller.project, self.local_tts_voices, self.controller.directory)

    def update_speaker_tts_voice(self, speaker_id, voice_id, speed):
        try:
            self.controller.update_speaker_tts_voice(speaker_id, voice_id, speed)
            self.refresh()
        except Exception as exc:
            self.error(exc)

    def update_audio_settings(self, orig_vol, dub_vol, add_path, add_vol, add_start):
        try:
            self.controller.update_audio_settings(
                original_volume=orig_vol,
                dubbed_volume=dub_vol,
                additional_audio_path=add_path,
                additional_audio_volume=add_vol,
                additional_audio_start=add_start,
            )
            self.pages[5].populate(self.controller.project, self.local_tts_voices, self.controller.directory)
            self.pages[6].populate(self.controller.project, self.controller.directory)
        except Exception as exc:
            self.error(exc)

    def clear_additional_audio(self):
        try:
            self.controller.clear_additional_audio()
            self.pages[5].populate(self.controller.project, self.local_tts_voices, self.controller.directory)
            self.pages[6].populate(self.controller.project, self.controller.directory)
        except Exception as exc:
            self.error(exc)

    def preview_local_tts_voice(self, voice_id):
        try:
            ready_ids = {voice.get("voice_id") for voice in (self.local_tts_voices or [])}
            if not voice_id or voice_id not in ready_ids:
                raise ValueError("Chọn một Local_TTS voice đang READY")
            self.start_job(
                lambda **job: self.controller.preview_local_tts_voice(voice_id, **job),
                self.pages[5].play_audio,
            )
        except Exception as exc:
            self.error(exc)

    def generate_tts(self):
        try:
            self.sync_options()
            self.controller.save()
            self.start_job(self.controller.generate_tts, self.accept_tts_generation)
        except Exception as exc:
            self.error(exc)

    def accept_tts_generation(self, result):
        self.controller.accept(self.controller.load(self.controller.directory / "project.json"))
        self.refresh()
        message = f"Generated {result.generated}, cached {result.cached}"
        if result.failed_ids:
            message += f"; failed: {', '.join(map(str, result.failed_ids))}"
        self.statusBar().showMessage(message, 10000)

    def mix_tts(self):
        try:
            self.sync_options()
            self.controller.save()
            self.start_job(self.controller.mix_tts, self.accept_tts_mix)
        except Exception as exc:
            self.error(exc)

    def accept_tts_mix(self, path):
        self.refresh()
        self.statusBar().showMessage(f"Dubbed audio mix hoàn tất: {path}", 10000)

    def mix_final_audio(self):
        try:
            self.sync_options()
            self.controller.save()
            if len(self.pages) > 5 and hasattr(self.pages[5], "stop_final_audio_playback"):
                self.pages[5].stop_final_audio_playback(release_source=True)
            self.start_job(self.controller.mix_final_audio, self.accept_final_audio_mix)
        except Exception as exc:
            self.error(exc)

    def accept_final_audio_mix(self, path):
        self.refresh()
        self.statusBar().showMessage(f"Final audio mix hoàn tất: {path}", 10000)

    def render_export_video(self, is_test_30s, start_time):
        try:
            if not self.save_project():
                return
            mode_desc = f"Test 30s (từ {start_time:.2f}s)" if is_test_30s else "Full Video"
            self.start_job(
                lambda **job: self.controller.render_export(test_mode=is_test_30s, start=start_time, **job),
                lambda path: self.pages[6].render_output_label.setText(f"Xuất video {mode_desc} hoàn tất: {path}"),
            )
        except Exception as exc:
            self.error(exc)

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
        msg = str(message)
        if "LOCAL_TTS_EXECUTABLE_NOT_FOUND" in msg:
            self.pages[5].tts_connection_status.setText("FAILED TO START LOCAL_TTS: Không tìm thấy file chạy Local_TTS. Bấm 'Select Local_TTS…'")
        elif "Local_TTS" in msg or "LOCAL_TTS" in msg:
            self.pages[5].tts_connection_status.setText(f"FAILED: {msg}")
        # A pipeline failure persists status while keeping the previous subtitle list.
        if self.controller.directory and self.worker is not None:
            try:
                self.controller.accept(self.controller.load(self.controller.directory / "project.json"))
                self.refresh()
            except (OSError, ValueError, TypeError):
                pass
        QMessageBox.critical(self, "Cartoon Sub", msg)

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
        self.controller.project.mask,self.controller.project.subtitle_style=self.pages[4].values()
        self.controller.project.logos,self.controller.project.watermark=self.pages[4].overlay_values()

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
        if self.controller.directory != result[1]:
            self.pages[4].reset_media()
            self.pages[5].reset_media()
            self.pages[6].reset_media()
            self.local_tts_voices = None
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
        for index in range(1, 7):
            self.tabs.setTabEnabled(index, project is not None)
        self.save_action.setEnabled(project is not None)
        if project is None:
            return
        refresh_timeline(project)
        self.pages[4].load_project(project)
        self.pages[0].metadata.setPlainText(project.source_video_path + "\n\n" + json.dumps(project.metadata, ensure_ascii=False, indent=2))
        page = self.pages[2]
        page.preset.setCurrentIndex(max(0, page.preset.findData(project.translation_preset)))
        page.prompt.setPlainText(project.translation_prompt)
        page.glossary.setPlainText("\n".join(f"{k} -> {v}" for k, v in project.glossary.items()))
        for key, check in page.genres.items():
            check.setChecked(key in project.translation_genres)
        context_ready = bool(project.segments) and project.context_source_hash == source_fingerprint(project)
        ready = context_ready and review_complete(project)
        page.translate_button.setEnabled(ready)
        page.analyze_button.setEnabled(bool(project.segments) and review_complete(project))
        page.context_button.setEnabled(bool(project.segments))
        page.proposal_button.setEnabled(bool(project.context_proposal))
        context = project.story_context
        status = "Hồ sơ đã áp dụng cho transcript này" if ready else "Cần duyệt đề xuất hoặc tự nhập và áp dụng hồ sơ"
        if not review_complete(project):status="Cần duyệt/gán speaker trong Transcript trước khi dịch. " + status
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
        self.pages[1].speaker_button.setEnabled(bool(project.segments))
        table = self.pages[1].table
        table.setRowCount(len(project.segments))
        for row, s in enumerate(project.segments):
            values = [s.id, f"{s.start:.3f}", f"{s.end:.3f}", f"{s.duration:.3f}",
                f"{s.speaker_id} · {s.speaker_name}", s.overlap_group or "No", s.zh]
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setToolTip(str(value))
                table.setItem(row, col, item)
        table.resizeRowsToContents()
        self.pages[3].load_project(project)
        audio = self.pages[5]
        audio.set_settings(self.controller.settings_store.load_local_tts())
        audio.populate(project, self.local_tts_voices, self.controller.directory)
        export = self.pages[6]
        export.populate(project, self.controller.directory)
        self.refresh_timeline_table()
        self.statusBar().showMessage(f"{project.name}: {len(project.segments)} subtitles — {project.transcription_status}")

    def closeEvent(self, event):
        if len(self.pages) > 5 and hasattr(self.pages[5], "stop_final_audio_playback"):
            self.pages[5].stop_final_audio_playback(release_source=True)
        if self.worker is not None:
            self.worker.cancel()
            self.statusBar().showMessage("Đang hủy job; đóng lại sau khi hoàn tất.")
            event.ignore()
        elif self.save_project():
            self.controller.shutdown_local_tts()
            event.accept()
        else:
            event.ignore()
