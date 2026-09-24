from copy import deepcopy
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QKeySequence, QUndoCommand, QUndoStack
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QHeaderView, QHBoxLayout, QInputDialog,
    QLabel, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from cartoon_sub.speaker.models import Speaker


def next_speaker_id(registry):
    numbers = [int(key[4:]) for key in registry if key.startswith("SPK_") and key[4:].isdigit()]
    return f"SPK_{max(numbers, default=0) + 1:02d}"


class _WorkingSpeakerCommand(QUndoCommand):
    def __init__(self, dialog, before, after, label):
        super().__init__(label)
        self.dialog = dialog
        self.before = before
        self.after = after

    def undo(self):
        self.dialog._apply_working_state(*self.before)

    def redo(self):
        self.dialog._apply_working_state(*self.after)


class SpeakerDialog(QDialog):
    """Speaker-only working state; the canonical project changes only on Confirm or Reset."""

    def __init__(self, project, directory, parent=None, reset_callback=None):
        super().__init__(parent)
        self.source_project = project
        self.directory = Path(directory)
        self.reset_callback = reset_callback
        self.speakers = deepcopy(project.speakers)
        self.assignments = {row.id: row.speaker_id for row in project.utterances}
        baseline = project.speaker_review_initial_state.get("assignments", {})
        self.original_assignments = {
            row.id: baseline.get(str(row.id), row.speaker_id) for row in project.utterances
        }
        self.undo_stack = QUndoStack(self)
        undo_action = self.undo_stack.createUndoAction(self, "Hoàn tác Speaker Review")
        redo_action = self.undo_stack.createRedoAction(self, "Làm lại Speaker Review")
        undo_action.setShortcut(QKeySequence.StandardKey.Undo)
        redo_action.setShortcuts((QKeySequence.StandardKey.Redo, QKeySequence("Ctrl+Shift+Z")))
        self.addActions((undo_action, redo_action))
        self.setWindowTitle("Speaker review — mỗi dòng là một utterance độc lập")
        self.resize(1150, 720)
        box = QVBoxLayout(self)
        note = QLabel(
            "Speaker là stable ID cho giọng nói. Tạo SPK mới, đổi tên và gán các dòng; "
            "SPK_UNKNOWN phải được xử lý trước khi xác nhận."
        )
        note.setWordWrap(True); box.addWidget(note)

        selectors = QHBoxLayout()
        self.filter = QComboBox()
        selectors.addWidget(QLabel("Lọc")); selectors.addWidget(self.filter)
        selectors.addStretch()
        box.addLayout(selectors)

        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels([
            "ID", "Start", "End", "Duration", "Speaker original", "Speaker",
            "Overlap", "Chinese", "AI confidence",
        ])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setColumnWidth(7, 350); self.table.setWordWrap(True)
        self.table.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().sectionResized.connect(lambda *_: self.table.resizeRowsToContents())
        box.addWidget(self.table)

        actions = QHBoxLayout()
        self.select_all_button = QPushButton("Chọn tất cả")
        self.new_button = QPushButton("Speaker mới")
        self.rename_button = QPushButton("Đổi tên")
        self.assign_button = QPushButton("Gán dòng cho SPK")
        self.play_button = QPushButton("Nghe dòng chọn")
        for button, action in (
            (self.select_all_button, self.select_visible), (self.new_button, self.add),
            (self.rename_button, self.rename), (self.assign_button, self.assign),
            (self.play_button, self.play),
        ):
            button.clicked.connect(action); actions.addWidget(button)
        box.addLayout(actions)

        commits = QHBoxLayout()
        self.reset_button = QPushButton("Reset")
        self.confirm_button = QPushButton("Xác nhận Speaker và Lưu")
        self.cancel_button = QPushButton("Hủy")
        self.reset_button.clicked.connect(self.reset_to_initial)
        self.confirm_button.clicked.connect(self.approve)
        self.cancel_button.clicked.connect(self.reject)
        for button in (self.reset_button, self.confirm_button, self.cancel_button):
            commits.addWidget(button)
        box.addLayout(commits)

        self.player = QMediaPlayer(self); self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.player.errorOccurred.connect(lambda *args: QMessageBox.warning(self, "Audio", self.player.errorString()))
        self.end_ms = self.start_ms = 0; self.play_pending = False
        self.player.mediaStatusChanged.connect(self.loaded)
        self.timer = QTimer(self); self.timer.setInterval(50); self.timer.timeout.connect(self.check_playback)
        self.filter.currentIndexChanged.connect(self.fill)
        self.refresh()

    def _load_project_speaker_state(self, project):
        self.source_project = project
        self.speakers = deepcopy(project.speakers)
        self.assignments = {row.id: row.speaker_id for row in project.utterances}

    def _working_state(self):
        return deepcopy(self.speakers), dict(self.assignments)

    def _apply_working_state(self, speakers, assignments):
        self.speakers = deepcopy(speakers)
        self.assignments = dict(assignments)
        self.refresh()

    def ids(self):
        return [int(self.table.item(index.row(), 0).text())
                for index in self.table.selectionModel().selectedRows()]

    def select_visible(self):
        self.table.selectAll()

    def refresh(self):
        counts = {key: 0 for key in self.speakers}
        for speaker_id in self.assignments.values():
            counts[speaker_id] = counts.get(speaker_id, 0) + 1
        current_filter = self.filter.currentData()
        self.filter.blockSignals(True)
        self.filter.clear(); self.filter.addItem("Tất cả", None)
        for speaker_id, info in self.speakers.items():
            label = f"{speaker_id} · {info['name']} ({counts.get(speaker_id, 0)} câu)"
            self.filter.addItem(label, speaker_id)
        self.filter.setCurrentIndex(max(0, self.filter.findData(current_filter)))
        self.filter.blockSignals(False)
        self.fill()

    def _speaker_options(self, excluded_ids=None):
        excluded_ids = set(excluded_ids or ())
        counts = {key: 0 for key in self.speakers}
        for speaker_id in self.assignments.values():
            counts[speaker_id] = counts.get(speaker_id, 0) + 1
        return [
            (f"{speaker_id} · {info['name']} ({counts.get(speaker_id, 0)} câu)", speaker_id)
            for speaker_id, info in self.speakers.items() if speaker_id not in excluded_ids
        ]

    def _choose_speaker(self, title, excluded_ids=None):
        options = self._speaker_options(excluded_ids)
        if not options:
            QMessageBox.information(self, "Speaker", "Không có speaker đích phù hợp.")
            return None
        labels = [label for label, _ in options]
        selected, accepted = QInputDialog.getItem(
            self, title, "Chọn speaker:", labels, 0, False,
        )
        return dict(options).get(selected) if accepted else None

    def fill(self):
        selected_filter = self.filter.currentData()
        rows = [row for row in self.source_project.utterances
                if not selected_filter or self.assignments[row.id] == selected_filter]
        self.table.setRowCount(len(rows))
        for row_index, utterance in enumerate(rows):
            speaker_id = self.assignments[utterance.id]
            overlap = utterance.overlap_group or (
                utterance.overlap_type if utterance.overlap_type != "NONE" else "No"
            )
            values = [utterance.id, f"{utterance.start:.3f}", f"{utterance.end:.3f}",
                      f"{utterance.duration:.3f}", self.original_assignments[utterance.id],
                      speaker_id, overlap, utterance.zh,
                      utterance.speaker_confidence if utterance.speaker_confidence is not None else "Unknown"]
            for column, value in enumerate(values):
                self.table.setItem(row_index, column, QTableWidgetItem(str(value)))
        self.table.resizeRowsToContents()

    def add(self):
        speaker_id = next_speaker_id(self.speakers)
        after_speakers, after_assignments = self._working_state()
        after_speakers[speaker_id] = Speaker(speaker_id).__dict__.copy()
        self.undo_stack.push(_WorkingSpeakerCommand(
            self, self._working_state(), (after_speakers, after_assignments),
            "Tạo speaker",
        ))

    def rename(self):
        speaker_id = self._choose_speaker("Đổi tên speaker")
        if not speaker_id:
            return
        name, accepted = QInputDialog.getText(
            self, "Tên speaker", "Tên hiển thị", text=self.speakers[speaker_id]["name"],
        )
        if accepted and name.strip():
            after_speakers, after_assignments = self._working_state()
            after_speakers[speaker_id]["name"] = name.strip()
            self.undo_stack.push(_WorkingSpeakerCommand(
                self, self._working_state(), (after_speakers, after_assignments),
                "Đổi tên speaker",
            ))

    def assign(self):
        ids = self.ids()
        if not ids:
            QMessageBox.warning(self, "Speaker", "Vui lòng chọn ít nhất một dòng cần gán speaker.")
            return
        current_ids = {self.assignments[utterance_id] for utterance_id in ids}
        excluded = current_ids if len(current_ids) == 1 else set()
        speaker_id = self._choose_speaker("Gán dòng sang speaker", excluded)
        if not speaker_id:
            return
        after_speakers, after_assignments = self._working_state()
        for utterance_id in ids:
            after_assignments[utterance_id] = speaker_id
        self.undo_stack.push(_WorkingSpeakerCommand(
            self, self._working_state(), (after_speakers, after_assignments),
            "Gán dòng cho speaker",
        ))

    def approve(self):
        self.stop_playback()
        if any(speaker_id == "SPK_UNKNOWN" for speaker_id in self.assignments.values()):
            QMessageBox.warning(self, "Speaker", "Còn dòng chưa gán speaker. Hãy gán SPK trước khi xác nhận.")
            return
        try:
            for key, raw in self.speakers.items():
                speaker = Speaker(**raw)
                if speaker.id != key:
                    raise ValueError("Speaker registry ID mismatch")
        except ValueError as exc:
            QMessageBox.warning(self, "Speaker", str(exc)); return
        self.accept()

    def reset_to_initial(self):
        answer = QMessageBox.question(
            self, "Reset Speaker Review",
            "Khôi phục Speaker Review về trạng thái ban đầu sau Transcript?\n"
            "Mọi chỉnh sửa speaker đã lưu sẽ bị thay thế.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        if self.reset_callback is None:
            QMessageBox.warning(self, "Speaker", "Không thể Reset vì project chưa có persistence callback"); return
        self._load_project_speaker_state(self.reset_callback())
        self.undo_stack.clear()
        self.refresh()

    def play(self):
        self.stop_playback()
        ids = self.ids()
        if not ids:
            return
        utterance = next(row for row in self.source_project.utterances if row.id == ids[0])
        source = self.directory / "audio" / "source.wav"
        if not source.exists(): source = Path(self.source_project.source_video_path)
        if not source.exists():
            QMessageBox.warning(self, "Audio", "Không tìm thấy audio/video nguồn"); return
        self.start_ms, self.end_ms = round(utterance.start * 1000), round(utterance.end * 1000)
        self.play_pending = True
        url = QUrl.fromLocalFile(str(source.resolve()))
        if self.player.source() != url:
            self.player.setSource(url)
        elif self.player.mediaStatus() in (
            QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia,
            QMediaPlayer.MediaStatus.EndOfMedia,
        ):
            self.loaded(QMediaPlayer.MediaStatus.LoadedMedia)

    def loaded(self, status):
        if status in (QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia) \
                and self.play_pending and self.end_ms:
            self.play_pending = False
            self.player.setPosition(self.start_ms); self.player.play(); self.timer.start()

    def check_playback(self):
        if self.end_ms and self.player.position() >= self.end_ms: self.stop_playback()

    def stop_playback(self):
        self.play_pending = False; self.end_ms = self.start_ms = 0
        self.timer.stop(); self.player.stop()

    def done(self, result):
        self.stop_playback(); super().done(result)

    def hideEvent(self, event):
        self.stop_playback(); super().hideEvent(event)
