import json
import logging
import re
from pathlib import Path
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence, QUndoStack
from PySide6.QtWidgets import (QMainWindow, QTabWidget, QFileDialog, QMessageBox,
    QProgressBar, QPushButton, QInputDialog, QLabel, QScrollArea, QWidget, QHBoxLayout, QVBoxLayout)
from cartoon_sub.app.controller import Controller
from cartoon_sub.ui.worker import Worker
from cartoon_sub.ui.no_wheel import NoWheelNumericFilter
from cartoon_sub.ui.settings_dialog import SettingsDialog
from cartoon_sub.ui.context_dialog import ContextDialog
from cartoon_sub.translation.context_service import source_fingerprint, context_config_fingerprint
from cartoon_sub.speaker.service import review_complete, refresh_timeline
from cartoon_sub.ui.speaker_dialog import SpeakerDialog
from cartoon_sub.ui.dubbing_settings_dialog import DubbingSettingsDialog
from cartoon_sub.ui.utterance_dialog import UtteranceDialog
from cartoon_sub.ui.docs_dialog import DocsWindow
from cartoon_sub.ui.timeline_table import populate, selected_ids, get_dirty_rows
from cartoon_sub.translation.dubbing_service import eligible_dubbing_ids, parse_dubbing_threshold
from cartoon_sub.ui.undo import AppliedValueCommand
from cartoon_sub.syllable.target import DubbingSettings
from cartoon_sub.ui.tabs import video_tab, transcript_tab, translate_tab, subtitle_tab, mask_style_tab, audio_tab, export_tab

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.controller = Controller()
        self.no_wheel_filter = NoWheelNumericFilter(self)
        from PySide6.QtWidgets import QApplication
        QApplication.instance().installEventFilter(self.no_wheel_filter)
        self.worker = None
        self.undo_stack = QUndoStack(self)
        self.undo_action = self.undo_stack.createUndoAction(self, "Undo")
        self.undo_action.setShortcuts([QKeySequence.StandardKey.Undo])
        self.redo_action = self.undo_stack.createRedoAction(self, "Redo")
        self.redo_action.setShortcuts([QKeySequence.StandardKey.Redo, QKeySequence("Ctrl+Shift+Z")])
        self.addAction(self.undo_action);self.addAction(self.redo_action)
        self.local_tts_voices = None
        self.local_tts_voice_revision = None
        self.voice_sync_worker = None
        self.voice_sync_offline = False
        self.voice_sync_timer = QTimer(self)
        self.voice_sync_timer.setInterval(3000)
        self.voice_sync_timer.timeout.connect(self.poll_local_tts_voices)
        self.setWindowTitle("Cartoon Sub — Master Timeline / Speaker & Dubbing")
        screen = self.screen().availableGeometry()
        self.resize(min(1100, screen.width()), min(750, screen.height()))
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)
        self.pages = [module.build() for module in (video_tab, transcript_tab, translate_tab, subtitle_tab, mask_style_tab, audio_tab, export_tab)]
        self.pages[4].set_undo_stack(self.undo_stack, self.persist_undo_state)
        self.tab_scrolls = []; self.tab_job_panels = []
        for name, widget in zip(("Video", "Transcript", "Translate", "Subtitle", "Mask", "Audio", "Export"), self.pages):
            scroll = QScrollArea(); scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            scroll.setWidget(widget); self.tab_scrolls.append(scroll)
            wrapper=QWidget(); wrapper_layout=QVBoxLayout(wrapper); wrapper_layout.setContentsMargins(0,0,0,0)
            wrapper_layout.addWidget(scroll,1)
            panel=QWidget(); row=QHBoxLayout(panel); row.setContentsMargins(6,3,6,3)
            label=QLabel("Running..."); bar=QProgressBar(); bar.setRange(0,0); cancel=QPushButton("Cancel")
            cancel.clicked.connect(self.cancel_job); row.addWidget(label); row.addWidget(bar,1); row.addWidget(cancel)
            panel.status_label=label; panel.progress_bar=bar; panel.cancel_button=cancel; panel.hide()
            wrapper_layout.addWidget(panel); self.tab_job_panels.append(panel)
            self.tabs.addTab(wrapper, name)
        self.tabs.currentChanged.connect(self.on_tab_changed)
        self.project_menu = self.menuBar().addMenu("Project")
        self.open_action = self.project_menu.addAction("Open project", self.load_project)
        self.save_action = self.project_menu.addAction("Save project", self.save_project)
        self.save_action.setShortcut("Ctrl+S")
        self.settings_menu = self.menuBar().addMenu("Settings")
        self.settings_menu.addAction("AI…", self.open_settings)
        self.settings_menu.addAction("Translation / Dubbing…", self.open_dubbing_settings)
        self.docs_button = QPushButton("Docs")
        self.docs_button.clicked.connect(self.show_docs)
        self.menuBar().setCornerWidget(self.docs_button, Qt.Corner.TopRightCorner)
        self.copyright_label = QLabel("© PHẠM THANH TÙNG - 0866891380")
        self.statusBar().addPermanentWidget(self.copyright_label)
        self.pages[0].open_button.clicked.connect(self.open_video)
        self.pages[1].import_button.clicked.connect(self.import_srt)
        self.pages[1].transcribe_button.clicked.connect(self.transcribe)
        self.pages[1].speaker_button.clicked.connect(self.edit_speakers)
        self.pages[1].export_transcript_button.clicked.connect(self.export_transcript_srt)
        self.pages[2].translate_button.clicked.connect(self.translate)
        self.pages[2].qa_button.clicked.connect(self.qa_translation)
        self.pages[2].import_vi_button.clicked.connect(self.import_vi_srt)
        self.pages[2].analyze_button.clicked.connect(self.analyze_context)
        self.pages[2].proposal_button.clicked.connect(lambda: self.edit_context(True))
        self.pages[2].view.currentIndexChanged.connect(self.refresh_timeline_table)
        self.pages[2].edit_button.clicked.connect(self.edit_utterance)
        self.pages[2].optimize_button.clicked.connect(self.optimize_dubbing)
        self.pages[2].revert_optimize_button.clicked.connect(self.revert_dubbing_optimization)
        self.pages[2].apply_edits_button.clicked.connect(self.apply_manual_edits)
        self.pages[2].revert_edits_button.clicked.connect(self.revert_manual_edits)
        self.pages[2].export_translate_button.clicked.connect(self.export_translate_srt)
        self.pages[2].export_speakers_button.clicked.connect(self.export_speakers)
        subtitle = self.pages[3]
        subtitle.apply_settings.clicked.connect(self.apply_segmentation_settings)
        subtitle.warning_filter.currentIndexChanged.connect(self.refresh_segmentation_page)
        subtitle.auto_all.clicked.connect(lambda: self.auto_segment(False))
        subtitle.auto_selected.clicked.connect(lambda: self.auto_segment(True))
        subtitle.refine_audio.clicked.connect(self.refine_display_timing)
        subtitle.split_manual.clicked.connect(self.split_display_segment)
        subtitle.merge.clicked.connect(self.merge_display_segments)
        subtitle.reset.clicked.connect(self.reset_segmentation)
        subtitle.export_subtitle_button.clicked.connect(self.export_subtitle_srt)
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
        audio.batch_voice_requested.connect(self.apply_batch_voice)
        audio.audio_settings_changed.connect(self.update_audio_settings)
        audio.clear_additional_requested.connect(self.clear_additional_audio)
        audio.mix_final_requested.connect(self.mix_final_audio)

        export = self.pages[6]
        export.render_requested.connect(self.render_export_video)
        self.refresh()
        QTimer.singleShot(150, lambda: self.auto_start_local_tts() if self.isVisible() else None)

    def show_docs(self):
        if not hasattr(self, "docs_window") or self.docs_window is None:
            self.docs_window = DocsWindow(self)
            self.docs_window.destroyed.connect(lambda *_: setattr(self, "docs_window", None))
        self.docs_window.show()
        self.docs_window.raise_()
        self.docs_window.activateWindow()

    def _project_state(self):
        return self.controller.project.to_dict() if self.controller.project else None

    def _restore_project_state(self,state):
        from cartoon_sub.subtitle.models import Project
        self.controller.project=Project.from_dict(state)
        if self.controller.directory:self.controller.save()
        self.refresh()

    def _record_project_edit(self,before,text):
        after=self._project_state()
        if before is not None and after is not None and before!=after:
            self.undo_stack.push(AppliedValueCommand(text,before,after,self._restore_project_state))

    def persist_undo_state(self):
        if self.controller.project and self.controller.directory:self.controller.save()
        self.refresh()

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
            before=self._project_state()
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
                    self.controller.save();self._record_project_edit(before,"Edit dubbing settings");self.refresh()
        except Exception as exc:self.error(exc)

    def edit_speakers(self):
        try:
            before=self._project_state();self.sync_options()
            dialog=SpeakerDialog(self.controller.project,self.controller.directory,self,
                                  reset_callback=self.reset_speaker_review)
            if dialog.exec()==dialog.DialogCode.Accepted:
                self.controller.commit_speaker_review(dialog.speakers,dialog.assignments)
                self._record_project_edit(before,"Edit speakers");self.refresh()
        except Exception as exc:self.error(exc)

    def reset_speaker_review(self):
        self.controller.reset_speaker_review()
        self.refresh()
        return self.controller.project

    def refresh_timeline_table(self):
        if self.controller.project:
            populate(self.pages[2].table,self.controller.project,self.pages[2].view.currentData())

    def refresh_segmentation_page(self):
        if self.controller.project:
            self.controller.sync_subtitle_presentation()
            self.pages[3].populate(self.controller.project)

    def on_tab_changed(self, index):
        if index == 3:
            self.refresh_segmentation_page()

    def edit_utterance(self):
        try:
            ids=selected_ids(self.pages[2].table)
            if not ids:raise ValueError("Chọn một câu trong bảng")
            segment=next(s for s in self.controller.project.segments if s.id==ids[0])
            dialog=UtteranceDialog(segment,self)
            if dialog.exec()==dialog.DialogCode.Accepted:
                before=self._project_state();self.sync_options()
                self.controller.edit_utterance(segment.id,dialog.subtitle.toPlainText(),dialog.dubbing.toPlainText(),dialog.mode.currentData(),dialog.target.value())
                self._record_project_edit(before,"Edit subtitle/dubbing text")
                self.refresh()
        except Exception as exc:self.error(exc)

    def optimize_dubbing(self):
        try:
            ids=selected_ids(self.pages[2].table)
            if not ids:raise ValueError("Chọn một hoặc nhiều câu trong bảng")
            dialog = QInputDialog(self)
            dialog.setWindowTitle("Tối ưu VI Dubbing")
            dialog.setLabelText("Ngưỡng Δ target tối thiểu")
            dialog.setInputMode(QInputDialog.InputMode.TextInput)
            dialog.setTextValue("+5")
            if dialog.exec() != dialog.DialogCode.Accepted:
                return
            threshold = parse_dubbing_threshold(dialog.textValue())
            self.sync_options();self.controller.save()
            eligible = eligible_dubbing_ids(self.controller.project, ids, threshold)
            if not eligible:
                self.statusBar().showMessage(
                    f"Không có câu đã chọn nào có Δ target từ +{threshold} trở lên; không gọi AI.", 7000,
                )
                return
            self.start_job(
                lambda **job:self.controller.optimize_dubbing(eligible, threshold=threshold, **job),
                self.accept_project,
            )
        except Exception as exc:self.error(exc)

    def revert_dubbing_optimization(self):
        try:
            ids = selected_ids(self.pages[2].table)
            if not ids:
                raise ValueError("Chọn một hoặc nhiều câu đã Optimize for dubbing")
            before=self._project_state();reverted = self.controller.revert_dubbing_optimization(ids);self._record_project_edit(before,"Revert dubbing optimization")
            self.refresh()
            self.statusBar().showMessage(f"Đã hoàn tác dubbing optimization cho {len(reverted)} câu.", 5000)
        except Exception as exc:self.error(exc)

    def apply_manual_edits(self):
        try:
            table = self.pages[2].table
            dirty_rows = get_dirty_rows(table)
            if not dirty_rows:
                return
            before=self._project_state();self.sync_options()
            self.controller.apply_manual_edits(dirty_rows)
            self._record_project_edit(before,"Edit master timeline")
            self.refresh()
            self.statusBar().showMessage(f"Đã áp dụng {len(dirty_rows)} dòng sửa tay vào master timeline.", 5000)
        except Exception as exc:
            self.error(exc)

    def revert_manual_edits(self):
        self.refresh_timeline_table()
        self.statusBar().showMessage("Đã hoàn tác các thay đổi chưa áp dụng trên bảng.", 5000)

    def apply_segmentation_settings(self):
        try:
            before=self._project_state()
            profile, settings = self.pages[3].values()
            self.controller.update_segmentation_settings(profile, settings)
            self.controller.save();self._record_project_edit(before,"Edit subtitle settings")
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
                before=self._project_state()
                self.controller.split_display_segment(*selected[0], word_index)
                self._record_project_edit(before,"Split subtitle")
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
            before=self._project_state();self.controller.merge_display_segments(utterance_ids.pop(), [display_id for _, display_id in selected])
            self._record_project_edit(before,"Merge subtitles")
            self.refresh()
        except Exception as exc:self.error(exc)

    def reset_segmentation(self):
        try:
            ids = self.pages[3].selected_utterance_ids()
            if not ids:
                raise ValueError("Chọn ít nhất một Utterance hoặc DisplaySegment để reset")
            before=self._project_state();self.controller.reset_segmentation(ids);self._record_project_edit(before,"Reset subtitles")
            self.refresh()
        except Exception as exc:self.error(exc)

    def export_speakers(self):
        try:
            if get_dirty_rows(self.pages[2].table):
                raise ValueError("Còn thay đổi chưa được áp dụng. Hãy Áp dụng bản sửa tay trước khi export.")
            self.sync_options();self.controller.save()
            text_type=self.pages[2].speaker_text_type.currentData()
            self.start_job(lambda **job:self.controller.export_speaker_files(text_type,**job),
                           lambda path:self.pages[2].export_speakers_status.setText("Exported: " + self._relative_project_path(path)))
        except Exception as exc:self.error(exc)

    def _relative_project_path(self, path):
        return Path(path).resolve().relative_to(Path(self.controller.directory).resolve()).as_posix()

    def export_transcript_srt(self):
        try:
            path = self.controller.export_transcript_srt()
            self.pages[1].export_transcript_status.setText("Exported: " + self._relative_project_path(path))
        except Exception as exc:self.error(exc)

    def export_translate_srt(self):
        try:
            if get_dirty_rows(self.pages[2].table):
                raise ValueError("Còn thay đổi chưa được áp dụng. Hãy Áp dụng bản sửa tay trước khi export.")
            path = self.controller.export_translate_srt()
            self.pages[2].export_translate_status.setText("Exported: " + self._relative_project_path(path))
        except Exception as exc:self.error(exc)

    def export_subtitle_srt(self):
        try:
            path = self.controller.export_subtitle_srt()
            self.pages[3].populate(self.controller.project)
            self.pages[3].export_subtitle_status.setText("Exported: " + self._relative_project_path(path))
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
        self.local_tts_voice_revision = result.get("revision")
        self.local_tts_voices = result["voices"]
        self.voice_sync_offline = False
        self._fallback_deleted_voice_mappings(result)
        page = self.pages[5]
        page.set_connection_result(result["health"], result["voices"])
        if self.controller.project:
            page.populate(self.controller.project, self.local_tts_voices, self.controller.directory)

    def poll_local_tts_voices(self):
        if self.voice_sync_worker is not None or not self.isVisible():
            return
        self.voice_sync_worker = Worker(
            lambda **job: self.controller.fetch_local_tts_voice_library(
                timeout_seconds=2.0, **job,
            ),
            self,
            log_errors=False,
        )
        self.voice_sync_worker.result.connect(self.accept_voice_library_sync)
        self.voice_sync_worker.error.connect(self.voice_library_sync_failed)
        self.voice_sync_worker.finished.connect(self.voice_library_sync_finished)
        self.voice_sync_worker.start()

    def accept_voice_library_sync(self, result):
        revision = result["revision"]
        if self.voice_sync_offline:
            logging.getLogger(__name__).info("[VOICE SYNC] Local_TTS reconnected")
        self.voice_sync_offline = False
        if revision == self.local_tts_voice_revision:
            return
        self.local_tts_voice_revision = revision
        self.local_tts_voices = result["voices"]
        self._fallback_deleted_voice_mappings(result)
        page = self.pages[5]
        page.set_connection_result({"status": "READY"}, self.local_tts_voices)
        if self.controller.project:
            page.populate(self.controller.project, self.local_tts_voices, self.controller.directory)

    def _fallback_deleted_voice_mappings(self, result):
        voices = result.get("voices") or []
        self.controller.fallback_deleted_tts_voice_mappings(
            voices, result.get("all_voice_ids") or [voice["voice_id"] for voice in voices],
        )

    def voice_library_sync_failed(self, message):
        if not self.voice_sync_offline:
            logging.getLogger(__name__).warning(
                "[VOICE SYNC] Local_TTS unavailable, retry in 3s: %s", message,
            )
        self.voice_sync_offline = True

    def voice_library_sync_finished(self):
        if self.voice_sync_worker is not None:
            self.voice_sync_worker.deleteLater()
            self.voice_sync_worker = None

    def showEvent(self, event):
        super().showEvent(event)
        if not self.voice_sync_timer.isActive():
            self.voice_sync_timer.start()
        QTimer.singleShot(0, self.poll_local_tts_voices)

    def update_speaker_tts_voice(self, speaker_id, voice_id, speed):
        try:
            before=self._project_state()
            self.controller.update_speaker_tts_voice(speaker_id, voice_id, speed)
            self._record_project_edit(before,"Change speaker voice")
            self.refresh()
        except Exception as exc:
            self.error(exc)

    def apply_batch_voice(self, speaker_ids, voice_id):
        try:
            before=self._project_state()
            for speaker_id in speaker_ids:
                speed = float(self.controller.project.speakers[speaker_id].get("tts_speed", 1.0))
                self.controller.update_speaker_tts_voice(speaker_id, voice_id, speed)
            self._record_project_edit(before,"Apply batch voice")
            self.refresh()
        except Exception as exc:
            self.error(exc)

    def update_audio_settings(self, orig_vol, dub_vol, add_path, add_vol, add_start):
        try:
            before=self._project_state()
            self.controller.update_audio_settings(
                original_volume=orig_vol,
                dubbed_volume=dub_vol,
                additional_audio_path=add_path,
                additional_audio_volume=add_vol,
                additional_audio_start=add_start,
            )
            self._record_project_edit(before,"Edit audio settings")
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
        message = (
            f"TTS cache: {result.cached} reused • "
            f"{result.generated}/{result.needed} generated"
        )
        if result.deleted:
            message += f" • {result.deleted} orphan deleted"
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
            from cartoon_sub.subtitle.models import Project
            project_snapshot = Project.from_dict(self.controller.project.to_dict())
            mode_desc = f"Test 30s (từ {start_time:.2f}s)" if is_test_30s else "Full Video"
            self.start_job(
                lambda **job: self.controller.render_export(
                    test_mode=is_test_30s, start=start_time, project_snapshot=project_snapshot, **job),
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
        popup_title = "Cartoon Sub"
        if msg.startswith("NO_AUDIO_STREAM"):
            popup_title = "Video không có âm thanh"
            msg = msg.removeprefix("NO_AUDIO_STREAM").lstrip("\n: ")
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
        QMessageBox.critical(self, popup_title, msg)

    def start_job(self, operation, accept):
        if self.worker is not None:
            self.statusBar().showMessage("Một tác vụ đang chạy; hãy đợi hoặc bấm Cancel.", 5000)
            return False
        self.active_job_tab = self.tabs.currentIndex()
        panel = self.tab_job_panels[self.active_job_tab]
        panel.status_label.setText("Running..."); panel.progress_bar.setRange(0,0)
        panel.cancel_button.setText("Cancel"); panel.cancel_button.setEnabled(True); panel.show()
        self.worker = Worker(operation, self)
        self.worker.result.connect(accept)
        self.worker.error.connect(self.error)
        self.worker.progress.connect(self.update_job_progress)
        self.worker.finished.connect(self.job_finished)
        self.worker.start()
        return True

    def update_job_progress(self, message):
        panel = self.tab_job_panels[getattr(self, "active_job_tab", self.tabs.currentIndex())]
        panel.status_label.setText(message)
        match = re.search(r"(\d+)\s*/\s*(\d+)", message)
        if match and int(match.group(2)) > 0:
            panel.progress_bar.setRange(0, int(match.group(2)))
            panel.progress_bar.setValue(min(int(match.group(1)), int(match.group(2))))
        else:
            panel.progress_bar.setRange(0, 0)

    def cancel_job(self):
        if self.worker:
            panel = self.tab_job_panels[getattr(self, "active_job_tab", self.tabs.currentIndex())]
            panel.status_label.setText("Cancelling...")
            panel.cancel_button.setText("Cancelling..."); panel.cancel_button.setEnabled(False)
            self.worker.cancel()

    def job_finished(self):
        if self.worker.cancel_event.is_set() and self.controller.directory:
            try:
                self.controller.accept(self.controller.load(self.controller.directory / "project.json"))
                self.refresh()
            except (OSError, ValueError, TypeError):
                pass
        self.tab_job_panels[getattr(self, "active_job_tab", self.tabs.currentIndex())].hide()
        self.worker.deleteLater()
        self.worker = None

    def sync_options(self):
        if not self.controller.project:
            return
        page = self.pages[2]
        self.controller.update_translation_options(page.preset.currentData(), page.prompt.toPlainText(),
            page.glossary.toPlainText(), [key for key, check in page.genres.items() if check.isChecked()],
            page.proper_name_mode.currentData())
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
        try:
            metadata = self.controller.validate_source_media(video)
        except Exception as exc:
            self.error(exc)
            return
        directory = QFileDialog.getExistingDirectory(self, "Chọn thư mục project mới (không chứa project.json)")
        if directory:
            self.start_job(lambda **job: self.controller.create(video, directory, metadata=metadata, **job),
                           self.accept_project)

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
        project_changed = self.controller.directory != result[1]
        if project_changed:
            self.pages[4].reset_media()
            self.pages[5].reset_media()
            self.pages[6].reset_media()
            self.pages[1].export_transcript_status.clear()
            self.pages[2].export_translate_status.clear()
            self.pages[2].export_speakers_status.clear()
            self.pages[3].export_subtitle_status.clear()
            self.local_tts_voices = None
            self.undo_stack.clear()
        self.controller.accept(result)
        self.refresh()
        from pathlib import Path
        if not Path(self.controller.project.source_video_path).is_file():
            QMessageBox.warning(self, "Missing source", "Video gốc không còn ở đường dẫn đã lưu; dữ liệu subtitle vẫn mở được.")

    def import_srt(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import Chinese SRT", "", "Subtitles (*.srt)")
        if path:
            try:
                before=self._project_state();self.sync_options()
                self.controller.import_subtitles(path)
                self._record_project_edit(before,"Import Chinese SRT")
                self.refresh()
            except Exception as exc:
                self.error(exc)

    def import_vi_srt(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import Vietnamese SRT", "", "Subtitles (*.srt)")
        if path:
            try:
                before=self._project_state()
                result = self.controller.import_vietnamese_subtitles(path)
                self._record_project_edit(before,"Import Vietnamese SRT")
                self.refresh()
                QMessageBox.information(self, "Vietnamese SRT",
                    f"Imported: {result['imported']}\nUnmatched: {result['unmatched']}\nConflicts: {result['conflicts']}")
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

    def qa_translation(self):
        try:
            self.sync_options()
            self.controller.save()
            self.start_job(self.controller.qa_translation, self.accept_project)
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
            before=self._project_state();self.sync_options()
            project = self.controller.project
            if proposal and (project.context_proposal_hash != source_fingerprint(project)
                             or project.context_proposal_config_hash != context_config_fingerprint(project)):
                raise ValueError("Transcript hoặc cấu hình đã đổi; hãy phân tích lại ngữ cảnh")
            if proposal and project.visual_context_status == "proposal_ready":
                from cartoon_sub.translation.visual_context import visual_source_signature
                if project.visual_context_signature != visual_source_signature(project):
                    raise ValueError("Video hoặc timeline đã đổi; hãy phân tích lại visual context")
            context = project.context_proposal if proposal else project.story_context
            required_status = "proposal_ready" if proposal else "applied"
            dialog = ContextDialog(
                context, {s.id for s in project.segments}, self, proposal,
                visual_ready=project.visual_context_status == required_status,
            )
            if dialog.exec() == dialog.DialogCode.Accepted:
                self.controller.apply_context(dialog.result_context)
                self._record_project_edit(before,"Edit story context")
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
        self.controller.sync_subtitle_presentation()
        refresh_timeline(project)
        self.pages[4].load_project(project)
        self.pages[0].metadata.setPlainText(project.source_video_path + "\n\n" + json.dumps(project.metadata, ensure_ascii=False, indent=2))
        page = self.pages[2]
        page.preset.setCurrentIndex(max(0, page.preset.findData(project.translation_preset)))
        page.prompt.setPlainText(project.translation_prompt)
        page.glossary.setPlainText("\n".join(f"{k} -> {v}" for k, v in project.glossary.items()))
        for key, check in page.genres.items():
            check.setChecked(key in project.translation_genres)
        page.proper_name_mode.setCurrentIndex(max(0, page.proper_name_mode.findData(project.proper_name_mode)))
        page.update_context_description()
        source_hash = source_fingerprint(project)
        config_hash = context_config_fingerprint(project)
        approved = bool(project.context_source_hash)
        approved_fresh = (approved and project.context_source_hash == source_hash
                          and project.context_approved_config_hash == config_hash)
        candidate_ready = (bool(project.context_proposal)
                           and project.context_proposal_hash == source_hash
                           and project.context_proposal_config_hash == config_hash)
        if candidate_ready and project.visual_context_status == "proposal_ready":
            try:
                from cartoon_sub.translation.visual_context import visual_source_signature
                candidate_ready = project.visual_context_signature == visual_source_signature(project)
            except (OSError, ValueError):
                candidate_ready = False
        ready = bool(project.segments) and review_complete(project)
        page.translate_button.setEnabled(ready)
        page.qa_button.setEnabled(any(segment.vi_subtitle.strip() for segment in project.segments))
        page.analyze_button.setEnabled(bool(project.segments) and review_complete(project))
        page.proposal_button.setEnabled(candidate_ready)
        context = project.story_context
        if not approved:
            status = "Chưa có ngữ cảnh AI đã duyệt — bản dịch sẽ dùng trực tiếp ràng buộc user"
        elif approved_fresh:
            status = "Ngữ cảnh AI đã duyệt đang được dùng làm source-of-truth"
        else:
            status = "Ngữ cảnh AI đã duyệt có thể đã cũ so với cấu hình hoặc transcript hiện tại (STALE)"
        if not review_complete(project):status="Cần duyệt/gán speaker trong Transcript trước khi dịch. " + status
        if candidate_ready:
            status += " • Có candidate AI mới đang chờ duyệt & lưu"
        states = {"completed": "Hoàn tất", "not_started": "Chưa dịch", "stale": "Cần dịch cập nhật — cấu hình/nguồn đã đổi",
                  "running": "Đang dịch", "failed": "Lỗi — bấm tiếp tục", "cancelled": "Đã hủy — có thể tiếp tục"}
        qa_counts = {}
        for qa in project.translation_qa.values():
            qa_counts[qa.get("status", "UNKNOWN")] = qa_counts.get(qa.get("status", "UNKNOWN"), 0) + 1
        qa_summary = ", ".join(f"{key}: {value}" for key, value in sorted(qa_counts.items())) or "chưa chạy"
        page.summary.setText(f"{status}\nNhân vật: {len(context.get('characters', []))} • Thuật ngữ: {len(context.get('terms', []))} "
            f"• Quy tắc xưng hô: {len(context.get('address_rules', []))} • Visual theo ID: {len(context.get('visual_contexts', []))} "
            f"• Visual status: {project.visual_context_status} • Nghi vấn ngữ cảnh: {len(context.get('uncertainties', []))}\n"
            f"Bản dịch: {states.get(project.translation_status, project.translation_status)} • QA/QC: {qa_summary}")
        model = self.controller.settings_store.load().translation_model
        page.summary.setText(page.summary.text() + f"\nModel dịch/ngữ cảnh: {model}" +
            (" — model 2.5 có thể bị hạn chế; đổi trong Settings > AI." if "gemini-2.5-" in model else ""))
        self.pages[1].transcribe_button.setEnabled(project.transcription_status != "imported")
        self.pages[1].speaker_button.setEnabled(bool(project.segments))
        transcript_tab.populate(self.pages[1], project)
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
            self.voice_sync_timer.stop()
            if self.voice_sync_worker is not None:
                self.voice_sync_worker.cancel()
                self.voice_sync_worker.wait()
                self.voice_sync_worker.deleteLater()
                self.voice_sync_worker = None
            self.controller.shutdown_local_tts()
            event.accept()
        else:
            event.ignore()
