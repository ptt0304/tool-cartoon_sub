from pathlib import Path

from PySide6.QtCore import QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QComboBox,
    QAbstractSpinBox,
    QDoubleSpinBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QStandardItem


class AudioPage(QWidget):
    connection_requested = Signal(str)
    mapping_changed = Signal(str, object, float)
    preview_requested = Signal(str)
    generate_requested = Signal()
    mix_dubbed_requested = Signal()
    audio_settings_changed = Signal(int, int, object, int, float)
    clear_additional_requested = Signal()
    mix_final_requested = Signal()
    retry_start_requested = Signal()
    select_exe_requested = Signal()
    batch_voice_requested = Signal(list, object)

    def __init__(self):
        super().__init__()
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        self.container = QWidget()
        layout = QVBoxLayout(self.container)

        # ----------------------------------------------------
        # Section A: Local_TTS Connection
        # ----------------------------------------------------
        tts_group = QGroupBox("Local_TTS")
        tts_layout = QVBoxLayout(tts_group)
        conn_row = QHBoxLayout()
        conn_row.addWidget(QLabel("Local_TTS URL:"))
        self.tts_url = QLineEdit("http://127.0.0.1:8765")
        self.tts_test_button = QPushButton("Test connection")
        self.tts_retry_button = QPushButton("Auto Start")
        self.tts_select_exe_button = QPushButton("Select Local_TTS…")
        conn_row.addWidget(self.tts_url, 1)
        conn_row.addWidget(self.tts_test_button)
        conn_row.addWidget(self.tts_retry_button)
        conn_row.addWidget(self.tts_select_exe_button)
        tts_layout.addLayout(conn_row)
        self.tts_connection_status = QLabel("Chưa kiểm tra kết nối")
        self.tts_connection_status.setWordWrap(True)
        tts_layout.addWidget(self.tts_connection_status)

        # ----------------------------------------------------
        # Section B: Character Voice Mapping
        # ----------------------------------------------------
        self.tts_table = QTableWidget(0, 7)
        self.tts_table.setHorizontalHeaderLabels(("✓", "Speaker", "Character", "AI Voice", "Engine", "Speed", "Status"))
        self.tts_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.tts_table.setMinimumHeight(180)
        tts_layout.addWidget(self.tts_table)

        batch_row = QHBoxLayout()
        self.select_all_button = QPushButton("Select all")
        self.clear_selection_button = QPushButton("Clear selection")
        self.batch_voice = QComboBox()
        self.apply_batch_voice_button = QPushButton("Apply voice to checked speakers")
        batch_row.addWidget(self.select_all_button); batch_row.addWidget(self.clear_selection_button)
        batch_row.addWidget(QLabel("AI Voice:")); batch_row.addWidget(self.batch_voice, 1)
        batch_row.addWidget(self.apply_batch_voice_button)
        tts_layout.addLayout(batch_row)

        tts_actions = QHBoxLayout()
        self.tts_preview_button = QPushButton("Preview selected voice")
        self.tts_generate_button = QPushButton("Generate / Resume TTS")
        self.tts_mix_button = QPushButton("Build Dubbed Audio")
        tts_actions.addWidget(self.tts_preview_button)
        tts_actions.addWidget(self.tts_generate_button)
        tts_actions.addWidget(self.tts_mix_button)
        tts_layout.addLayout(tts_actions)

        self.tts_status = QLabel("Generated 0/0")
        self.tts_status.setWordWrap(True)
        self.tts_dubbed_status = QLabel("Dubbed audio: chưa tạo")
        self.tts_dubbed_status.setWordWrap(True)
        tts_layout.addWidget(self.tts_status)
        tts_layout.addWidget(self.tts_dubbed_status)
        layout.addWidget(tts_group)

        # ----------------------------------------------------
        # Section C: Audio Mixer (Original, Dubbed, Additional)
        # ----------------------------------------------------
        mixer_group = QGroupBox("AUDIO MIXER")
        mixer_layout = QVBoxLayout(mixer_group)

        # Original Audio
        orig_row = QHBoxLayout()
        orig_row.addWidget(QLabel("Original Audio Volume:"))
        self.orig_volume = QSpinBox()
        self.orig_volume.setRange(0, 100);self.orig_volume.setValue(100);self.orig_volume.setSuffix(" %")
        self.orig_volume.setKeyboardTracking(False)
        self.orig_volume.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        orig_row.addWidget(self.orig_volume);orig_row.addStretch()
        mixer_layout.addLayout(orig_row)

        # Dubbed Audio
        dub_row = QHBoxLayout()
        dub_row.addWidget(QLabel("Dubbed Audio Volume:  "))
        self.dub_volume = QSpinBox()
        self.dub_volume.setRange(0, 100);self.dub_volume.setValue(100);self.dub_volume.setSuffix(" %")
        self.dub_volume.setKeyboardTracking(False)
        self.dub_volume.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        dub_row.addWidget(self.dub_volume);dub_row.addStretch()
        mixer_layout.addLayout(dub_row)

        # Additional Audio
        add_box = QGroupBox("Additional Audio (Optional: BGM, Sound Effects, Narration)")
        add_layout = QVBoxLayout(add_box)
        file_row = QHBoxLayout()
        file_row.addWidget(QLabel("File:"))
        self.add_path_edit = QLineEdit()
        self.add_path_edit.setPlaceholderText("Not selected — skipped")
        self.add_browse_btn = QPushButton("Browse")
        self.add_clear_btn = QPushButton("Clear")
        file_row.addWidget(self.add_path_edit, 1)
        file_row.addWidget(self.add_browse_btn)
        file_row.addWidget(self.add_clear_btn)
        add_layout.addLayout(file_row)

        add_vol_row = QHBoxLayout()
        add_vol_row.addWidget(QLabel("Volume:"))
        self.add_volume = QSpinBox()
        self.add_volume.setRange(0, 100);self.add_volume.setValue(100);self.add_volume.setSuffix(" %")
        self.add_volume.setKeyboardTracking(False)
        self.add_volume.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        add_vol_row.addWidget(self.add_volume);add_vol_row.addStretch()
        add_layout.addLayout(add_vol_row)

        add_start_row = QHBoxLayout()
        add_start_row.addWidget(QLabel("Start Offset (seconds):"))
        self.add_start_spin = QDoubleSpinBox()
        self.add_start_spin.setRange(0.0, 86400.0)
        self.add_start_spin.setSingleStep(1.0)
        self.add_start_spin.setDecimals(3)
        self.add_start_spin.setValue(0.0)
        self.add_start_spin.setKeyboardTracking(False)
        add_start_row.addWidget(self.add_start_spin)
        add_start_row.addStretch()
        add_layout.addLayout(add_start_row)

        self.add_status_label = QLabel("Not selected — skipped")
        add_layout.addWidget(self.add_status_label)
        mixer_layout.addWidget(add_box)

        # ----------------------------------------------------
        # Section D: Final Audio
        # ----------------------------------------------------
        final_actions = QHBoxLayout()
        self.final_mix_btn = QPushButton("Build Final Audio")
        self.final_play_btn = QPushButton("Play Final Audio")
        final_actions.addWidget(self.final_mix_btn)
        final_actions.addWidget(self.final_play_btn)
        mixer_layout.addLayout(final_actions)

        self.final_status_label = QLabel("FINAL AUDIO: NOT GENERATED")
        self.final_status_label.setWordWrap(True)
        mixer_layout.addWidget(self.final_status_label)

        layout.addWidget(mixer_group)
        layout.addStretch()

        scroll.setWidget(self.container)
        main_vbox = QVBoxLayout(self)
        main_vbox.setContentsMargins(0, 0, 0, 0)
        main_vbox.addWidget(scroll)

        # Media Player for previews and playback
        self.player = None
        self.audio_output = None
        self._voices = None
        self._root = None
        self._project = None
        self._is_playing_final = False

        # Wire UI interactions
        self.tts_test_button.clicked.connect(lambda: self.connection_requested.emit(self.tts_url.text().strip()))
        self.tts_retry_button.clicked.connect(self.retry_start_requested.emit)
        self.tts_select_exe_button.clicked.connect(self.select_exe_requested.emit)
        self.tts_preview_button.clicked.connect(self._on_preview_clicked)
        self.tts_generate_button.clicked.connect(self.generate_requested.emit)
        self.tts_mix_button.clicked.connect(self.mix_dubbed_requested.emit)
        self.select_all_button.clicked.connect(lambda: self._set_all_checked(True))
        self.clear_selection_button.clicked.connect(lambda: self._set_all_checked(False))
        self.apply_batch_voice_button.clicked.connect(self._apply_batch_voice)

        self.orig_volume.valueChanged.connect(self._on_volume_changed)
        self.dub_volume.valueChanged.connect(self._on_volume_changed)
        self.add_volume.valueChanged.connect(self._on_volume_changed)
        self.add_start_spin.valueChanged.connect(self._on_volume_changed)
        self.add_browse_btn.clicked.connect(self._browse_additional_audio)
        self.add_clear_btn.clicked.connect(self._clear_additional_audio)

        self.final_mix_btn.clicked.connect(self._on_final_mix_clicked)
        self.final_play_btn.clicked.connect(self._play_final_audio)

    def _on_final_mix_clicked(self):
        self.stop_final_audio_playback(release_source=True)
        self.mix_final_requested.emit()

    def _on_preview_clicked(self):
        voice_id = self.selected_voice_id()
        if voice_id:
            self.preview_requested.emit(voice_id)

    def _on_volume_changed(self):
        orig = self.orig_volume.value()
        dub = self.dub_volume.value()
        add = self.add_volume.value()
        start = self.add_start_spin.value()
        path = self.add_path_edit.text().strip() or None

        self.audio_settings_changed.emit(orig, dub, path, add, start)

    def _browse_additional_audio(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Chọn file âm thanh bổ sung",
            "",
            "Audio (*.wav *.mp3 *.m4a *.aac *.flac *.ogg *.wma);;All Files (*)",
        )
        if path:
            self.add_path_edit.setText(path)
            self._on_volume_changed()

    def _clear_additional_audio(self):
        self.add_path_edit.clear()
        self.add_start_spin.setValue(0.0)
        self.clear_additional_requested.emit()

    def selected_voice_id(self):
        row = self.tts_table.currentRow()
        combo = self.tts_table.cellWidget(row, 3) if row >= 0 else None
        return combo.currentData() if combo else None

    def _set_all_checked(self, checked):
        for row in range(self.tts_table.rowCount()):
            item = self.tts_table.item(row, 0)
            if item: item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)

    def checked_speaker_ids(self):
        return [self.tts_table.item(row, 1).text() for row in range(self.tts_table.rowCount())
                if self.tts_table.item(row, 0) and self.tts_table.item(row, 0).checkState() == Qt.CheckState.Checked]

    def _apply_batch_voice(self):
        ids = self.checked_speaker_ids(); voice_id = self.batch_voice.currentData()
        if ids and voice_id:
            self.batch_voice_requested.emit(ids, voice_id)

    def set_settings(self, settings):
        if not self.tts_url.hasFocus():
            self.tts_url.setText(settings.base_url)

    def set_connection_result(self, health, voices):
        self._voices = list(voices)
        status = health.get("status", "UNKNOWN")
        self.tts_connection_status.setText(f"{status} — {len(voices)} READY voices")

    def _ordered_voices(self):
        return [voice for voice in (self._voices or []) if voice.get("status") == "READY"]

    @staticmethod
    def _voice_label(voice):
        return voice.get("display_name") or voice["voice_id"]

    @staticmethod
    def _add_disabled_combo_item(combo, label):
        item = QStandardItem(label)
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        combo.model().appendRow(item)

    def _populate_voice_combo(self, combo, current_voice_id=None, preserve_current=False):
        voices = self._ordered_voices()
        available = {voice["voice_id"]: voice for voice in voices}
        favorites = [voice for voice in voices if voice.get("favorite") is True]
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("— Chọn voice —" if not preserve_current else "— Chưa chọn —", None)
        self._add_disabled_combo_item(combo, "★ Giọng yêu thích")
        if favorites:
            for voice in favorites:
                combo.addItem(self._voice_label(voice), voice["voice_id"])
        else:
            self._add_disabled_combo_item(combo, "Không có giọng yêu thích")
        self._add_disabled_combo_item(combo, "Tất cả giọng")
        for voice in voices:
            combo.addItem(self._voice_label(voice), voice["voice_id"])
        if preserve_current and current_voice_id and current_voice_id not in available and not voices:
            prefix = "Missing voice" if self._voices is not None else "Saved voice"
            combo.addItem(f"{prefix}: {current_voice_id}", current_voice_id)
        if current_voice_id not in available and voices:
            current_voice_id = voices[0]["voice_id"]
        found = combo.findData(current_voice_id)
        combo.setCurrentIndex(found if found >= 0 else 0)
        combo.blockSignals(False)

    def populate(self, project, voices=None, project_dir=None):
        self._project = project
        self._root = Path(project_dir) if project_dir else None
        if voices is not None:
            self._voices = [v for v in voices if v.get("status") == "READY"]
        ready = {voice["voice_id"]: voice for voice in (self._voices or [])}
        selected_batch = self.batch_voice.currentData()
        self._populate_voice_combo(self.batch_voice, selected_batch)

        # Populate TTS mapping table
        self.tts_table.setRowCount(0)
        for row_index, speaker_id in enumerate(sorted(project.speakers)):
            speaker = project.speakers[speaker_id]
            self.tts_table.insertRow(row_index)
            check = QTableWidgetItem(); check.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            check.setCheckState(Qt.CheckState.Unchecked); self.tts_table.setItem(row_index, 0, check)
            self.tts_table.setItem(row_index, 1, QTableWidgetItem(speaker_id))
            self.tts_table.setItem(row_index, 2, QTableWidgetItem(speaker.get("name", "Unknown")))

            combo = QComboBox()
            saved = speaker.get("tts_voice_id")
            self._populate_voice_combo(combo, saved, preserve_current=True)
            self.tts_table.setCellWidget(row_index, 3, combo)

            engine = QLabel()
            self.tts_table.setCellWidget(row_index, 4, engine)

            speed = QDoubleSpinBox()
            speed.setRange(0.1, 3.0)
            speed.setSingleStep(0.05)
            speed.setDecimals(2)
            speed.setValue(float(speaker.get("tts_speed", 1.0)))
            speed.setKeyboardTracking(False)
            self.tts_table.setCellWidget(row_index, 5, speed)

            selected_voice = combo.currentData()
            status_lbl = QLabel(ready.get(selected_voice, {}).get(
                "status", "READY" if selected_voice in ready else ("MISSING" if selected_voice else "—"),
            ))
            self.tts_table.setCellWidget(row_index, 6, status_lbl)

            def changed(*_, sid=speaker_id, selector=combo, speed_box=speed, engine_label=engine, status_box=status_lbl):
                vid = selector.currentData()
                v = ready.get(vid, {})
                engine_label.setText(str(v.get("engine") or v.get("source") or "—"))
                status_box.setText(v.get("status", "READY" if vid in ready else ("MISSING" if vid else "—")))
                self.mapping_changed.emit(sid, vid, speed_box.value())

            combo.currentIndexChanged.connect(changed)
            speed.valueChanged.connect(changed)
            v = ready.get(combo.currentData(), {})
            engine.setText(str(v.get("engine") or v.get("source") or "—"))

        # TTS Status
        generated = sum(row.tts_generation_status in {"generated", "cached"} for row in project.utterances)
        failed = [row.id for row in project.utterances if row.tts_generation_status == "failed"]
        stale = sum(row.tts_generation_status == "stale" for row in project.utterances)
        sync_ok = sum(row.tts_alignment_diagnostic == "SYNC_OK" for row in project.utterances)
        auto_fit = sum(row.tts_alignment_diagnostic == "AUTO_FIT" for row in project.utterances)
        needs_review = sum(row.tts_alignment_diagnostic == "NEEDS_TIMING_REVIEW" for row in project.utterances)
        overlap_groups = len({row.overlap_group for row in project.utterances if row.overlap_group})

        status_text = (
            f"Generated: {generated}/{len(project.utterances)} • "
            f"Sync OK: {sync_ok} • Auto-fit: {auto_fit} • Needs review: {needs_review} • Overlap groups: {overlap_groups}"
        )
        if stale:
            status_text += f" • Stale: {stale}"
        if failed:
            status_text += f"\nFailed IDs: {', '.join(map(str, failed))}"
        self.tts_status.setText(status_text)

        # Dubbed mix status
        dubbed = self._root / "audio" / "tts" / "dubbed_mix.wav" if self._root else None
        if dubbed and dubbed.is_file():
            self.tts_dubbed_status.setText(f"Dubbed audio: READY ({dubbed})")
        else:
            self.tts_dubbed_status.setText("Dubbed audio: NOT GENERATED")

        # Mixer Settings
        audio_settings = getattr(project, "audio_settings", None)
        if audio_settings:
            self.orig_volume.blockSignals(True)
            self.dub_volume.blockSignals(True)
            self.add_volume.blockSignals(True)
            self.add_start_spin.blockSignals(True)

            self.orig_volume.setValue(audio_settings.original_volume)
            self.dub_volume.setValue(audio_settings.dubbed_volume)
            self.add_volume.setValue(audio_settings.additional_audio_volume)
            self.add_start_spin.setValue(audio_settings.additional_audio_start)

            if audio_settings.additional_audio_path:
                self.add_path_edit.setText(audio_settings.additional_audio_path)
                p = Path(audio_settings.additional_audio_path)
                if not p.is_absolute() and self._root:
                    p = self._root / p
                if p.is_file():
                    self.add_status_label.setText(f"READY: {p.name} (Start: {audio_settings.additional_audio_start:.2f}s, Volume: {audio_settings.additional_audio_volume}%)")
                else:
                    self.add_status_label.setText(f"FILE MISSING: {audio_settings.additional_audio_path}")
            else:
                self.add_path_edit.clear()
                self.add_status_label.setText("Not selected — skipped")

            self.orig_volume.blockSignals(False)
            self.dub_volume.blockSignals(False)
            self.add_volume.blockSignals(False)
            self.add_start_spin.blockSignals(False)

        # Final audio status
        final = self._root / "audio" / "final_audio.wav" if self._root else None
        f_status = getattr(project, "final_audio_status", "not_generated").upper()
        if final and final.is_file():
            self.final_play_btn.setEnabled(True)
            if f_status == "STALE":
                self.final_status_label.setText(f"FINAL AUDIO: STALE (cần tạo lại do cấu hình đã đổi)\n{final}")
            else:
                self.final_status_label.setText(f"FINAL AUDIO: READY\n{final}")
        else:
            if self._is_playing_final:
                self._stop_final_audio()
            self.final_play_btn.setEnabled(False)
            self.final_status_label.setText("FINAL AUDIO: NOT GENERATED")

    def _init_player(self):
        if self.player is None:
            self.player = QMediaPlayer(self)
            self.audio_output = QAudioOutput(self)
            self.player.setAudioOutput(self.audio_output)
            self.player.playbackStateChanged.connect(self._on_playback_state_changed)
            self.player.mediaStatusChanged.connect(self._on_media_status_changed)
            self.player.errorOccurred.connect(self._on_player_error)

    def _on_playback_state_changed(self, state):
        if state == QMediaPlayer.PlaybackState.StoppedState:
            if self._is_playing_final:
                self.stop_final_audio_playback(release_source=True)

    def _on_media_status_changed(self, status):
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            if self._is_playing_final:
                self.stop_final_audio_playback(release_source=True)

    def _on_player_error(self, error, error_string=""):
        if self._is_playing_final:
            self.stop_final_audio_playback(release_source=True)

    def stop_final_audio_playback(self, release_source: bool = True):
        self._is_playing_final = False
        self.final_play_btn.setText("Play Final Audio")
        if self.player is not None:
            self.player.stop()
            self.player.setPosition(0)
            if release_source:
                self.player.setSource(QUrl())

    def _stop_final_audio(self):
        self.stop_final_audio_playback(release_source=True)

    def play_audio(self, path):
        if self._is_playing_final:
            self.stop_final_audio_playback(release_source=True)
        self._init_player()
        self.player.stop()
        self.player.setSource(QUrl.fromLocalFile(str(Path(path).resolve())))
        self.player.setPosition(0)
        self.player.play()

    def _play_final_audio(self):
        final = self._root / "audio" / "final_audio.wav" if self._root else None
        if not final or not final.is_file():
            return
        if self._is_playing_final:
            self.stop_final_audio_playback(release_source=True)
        else:
            self._init_player()
            self._is_playing_final = True
            self.final_play_btn.setText("Stop Final Audio")
            self.player.stop()
            self.player.setSource(QUrl.fromLocalFile(str(final.resolve())))
            self.player.setPosition(0)
            self.player.play()

    def reset_media(self):
        self.stop_final_audio_playback(release_source=True)

    def closeEvent(self, event):
        self.stop_final_audio_playback(release_source=True)
        super().closeEvent(event)


def build():
    return AudioPage()
