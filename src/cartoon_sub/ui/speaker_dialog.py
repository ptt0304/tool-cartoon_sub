from copy import deepcopy
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QBrush, QKeySequence, QShortcut, QUndoCommand, QUndoStack
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFormLayout, QGridLayout, QHeaderView, QHBoxLayout,
    QInputDialog, QLabel, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea,
    QSizePolicy, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from cartoon_sub.speaker.models import Speaker
from cartoon_sub.speaker.resolution_service import SpeakerResolutionService
from cartoon_sub.speaker.utterance_split_service import split_utterance
from cartoon_sub.subtitle.models import Project
from cartoon_sub.subtitle.timestamps import format_srt_timestamp


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


class UtteranceSplitDialog(QDialog):
    """Small editor for one explicit binary text/time/speaker boundary."""

    def __init__(self, utterance, speaker_options, preview_callback, parent=None):
        super().__init__(parent)
        self.utterance = utterance
        self.setWindowTitle(f"Tách dòng ID {utterance.id}")
        self.resize(680, 480)
        box = QVBoxLayout(self)
        box.addWidget(QLabel(f"Start: {utterance.start:.3f}   End: {utterance.end:.3f}"))
        self.chinese = QPlainTextEdit(utterance.zh)
        self.chinese.setReadOnly(True)
        self.chinese.setMaximumHeight(100)
        box.addWidget(QLabel("Chinese (chỉ xem):")); box.addWidget(self.chinese)

        form = QFormLayout()
        self.boundary = QSpinBox()
        self.boundary.setRange(1, max(1, len(utterance.zh) - 1))
        self.boundary.setValue(max(1, len(utterance.zh) // 2))
        self.timestamp = QDoubleSpinBox()
        self.timestamp.setDecimals(3); self.timestamp.setSingleStep(.010)
        self.timestamp.setRange(utterance.start + .001, utterance.end - .001)
        self.timestamp.setValue((utterance.start + utterance.end) / 2)
        self.left_speaker = QComboBox(); self.right_speaker = QComboBox()
        for label, speaker_id in speaker_options:
            self.left_speaker.addItem(label, speaker_id)
            self.right_speaker.addItem(label, speaker_id)
        current = self.left_speaker.findData(utterance.speaker_id)
        self.left_speaker.setCurrentIndex(max(0, current))
        self.right_speaker.setCurrentIndex(max(0, current))
        form.addRow("Ranh giới ký tự:", self.boundary)
        form.addRow("Mốc thời gian:", self.timestamp)
        form.addRow("Speaker phần trái:", self.left_speaker)
        form.addRow("Speaker phần phải:", self.right_speaker)
        box.addLayout(form)
        self.left_preview = QLabel(); self.right_preview = QLabel()
        self.left_preview.setWordWrap(True); self.right_preview.setWordWrap(True)
        box.addWidget(QLabel("Phần trái:")); box.addWidget(self.left_preview)
        box.addWidget(QLabel("Phần phải:")); box.addWidget(self.right_preview)
        preview = QPushButton("Nghe audio dòng gốc")
        preview.clicked.connect(lambda: preview_callback(utterance.start, utterance.end))
        box.addWidget(preview)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
        )
        buttons.accepted.connect(self._validate_and_accept); buttons.rejected.connect(self.reject)
        box.addWidget(buttons)
        self.boundary.valueChanged.connect(self._update_preview)
        self._update_preview()

    def _update_preview(self):
        boundary = self.boundary.value()
        self.left_preview.setText(self.utterance.zh[:boundary])
        self.right_preview.setText(self.utterance.zh[boundary:])

    def _validate_and_accept(self):
        if not self.left_preview.text().strip() or not self.right_preview.text().strip():
            QMessageBox.warning(self, "Tách dòng", "Hai phần tiếng Trung đều phải có nội dung.")
            return
        self.accept()


class SpeakerDialog(QDialog):
    """Speaker-only working state; the canonical project changes only on Confirm or Reset."""

    def __init__(self, project, directory, parent=None, reset_callback=None):
        super().__init__(parent)
        self.original_project = project
        self.working_project = Project.from_dict(project.to_dict())
        self.source_project = self.working_project
        self.directory = Path(directory)
        self.reset_callback = reset_callback
        self.speakers = deepcopy(project.speakers)
        self.speakers.setdefault("SPK_UNKNOWN", Speaker("SPK_UNKNOWN").__dict__.copy())
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
        root = QVBoxLayout(self)
        self.page_scroll = QScrollArea(self)
        self.page_scroll.setObjectName("speakerReviewPageScroll")
        self.page_scroll.setWidgetResizable(True)
        self.page_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.page_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.page_content = QWidget()
        self.page_content.setObjectName("speakerReviewPageContent")
        box = QVBoxLayout(self.page_content)
        self.page_scroll.setWidget(self.page_content)
        root.addWidget(self.page_scroll, 1)
        self.capability_status = QLabel(SpeakerResolutionService().summarize(project))
        self.capability_status.setWordWrap(True)
        box.addWidget(self.capability_status)
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

        box.addWidget(QLabel("Cụm speaker AI đề xuất — áp dụng vào bản nháp, sau đó kiểm tra và xác nhận toàn bộ:"))
        self.cluster_table = QTableWidget(0, 6)
        self.cluster_table.setHorizontalHeaderLabels([
            "SPK đề xuất", "Nhân vật", "Nhãn ngữ nghĩa", "Người được nói tới",
            "Utterance", "Confidence",
        ])
        self.cluster_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.cluster_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.cluster_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.cluster_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.cluster_table.setMinimumHeight(120)
        self.cluster_table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        clusters = project.speaker_proposals.get("clusters", [])
        self.cluster_table.setRowCount(len(clusters))
        for row_index, cluster in enumerate(clusters):
            values = [
                cluster.get("speaker_id", "SPK_UNKNOWN"), cluster.get("character_id", "UNKNOWN"),
                cluster.get("semantic_label", ""), ", ".join(cluster.get("addressee_ids", [])),
                ", ".join(map(str, cluster.get("utterance_ids", []))),
                cluster.get("confidence", "Unknown"),
            ]
            for column, value in enumerate(values):
                self.cluster_table.setItem(row_index, column, QTableWidgetItem(str(value)))
        box.addWidget(self.cluster_table, 1)

        self.proposals = {
            row.get("utterance_id"): row
            for row in project.speaker_proposals.get("utterances", [])
            if isinstance(row, dict)
        }
        self.table = QTableWidget(0, 15)
        self.table.setHorizontalHeaderLabels([
            "ID", "Start", "End", "Duration", "Speaker original", "Speaker",
            "Overlap", "Chinese", "AI confidence", "SPK đề xuất", "Nhân vật",
            "Người được nói tới", "Fusion", "Trạng thái", "Lý do / bất định",
        ])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setColumnWidth(7, 350); self.table.setWordWrap(True)
        self.table.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.table.setMinimumHeight(300)
        self.table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().sectionResized.connect(lambda *_: self.table.resizeRowsToContents())
        box.addWidget(self.table, 4)

        self.actions_widget = QWidget()
        self.actions_grid = QGridLayout(self.actions_widget)
        self.actions_grid.setContentsMargins(0, 0, 0, 0)
        self.select_all_button = QPushButton("Chọn tất cả")
        self.new_button = QPushButton("Speaker mới")
        self.rename_button = QPushButton("Đổi tên")
        self.assign_button = QPushButton("Gán dòng cho SPK")
        self.cluster_button = QPushButton("Áp dụng cluster chọn")
        self.proposal_button = QPushButton("Áp dụng mọi proposal")
        self.play_button = QPushButton("Nghe dòng chọn")
        self.split_button = QPushButton("Tách dòng")
        self.action_buttons = []
        for button, action in (
            (self.select_all_button, self.select_visible), (self.new_button, self.add),
            (self.rename_button, self.rename), (self.assign_button, self.assign),
            (self.cluster_button, self.apply_selected_cluster),
            (self.proposal_button, self.apply_proposals),
            (self.play_button, self.play),
            (self.split_button, self.split_selected),
        ):
            button.clicked.connect(action)
            self.action_buttons.append(button)
        box.addWidget(self.actions_widget)

        commits = QHBoxLayout()
        self.reset_button = QPushButton("Reset")
        self.confirm_button = QPushButton("Xác nhận Speaker và Lưu")
        self.cancel_button = QPushButton("Hủy")
        self.reset_button.clicked.connect(self.reset_to_initial)
        self.confirm_button.clicked.connect(self.approve)
        self.cancel_button.clicked.connect(self.reject)
        for button in (self.reset_button, self.confirm_button, self.cancel_button):
            commits.addWidget(button)
        root.addLayout(commits)

        self.player = QMediaPlayer(self); self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.player.errorOccurred.connect(lambda *args: QMessageBox.warning(self, "Audio", self.player.errorString()))
        self.end_ms = self.start_ms = 0; self.play_pending = False
        self.player.mediaStatusChanged.connect(self.loaded)
        self.timer = QTimer(self); self.timer.setInterval(50); self.timer.timeout.connect(self.check_playback)
        self.filter.currentIndexChanged.connect(self.fill)
        self.refresh()
        self._install_page_navigation()
        self._fit_to_available_screen()
        self._reflow_action_buttons()

    def _available_screen_geometry(self):
        screen = None
        parent = self.parentWidget()
        if parent is not None:
            handle = parent.windowHandle()
            screen = handle.screen() if handle is not None else QApplication.screenAt(parent.frameGeometry().center())
        return (screen or self.screen() or QApplication.primaryScreen()).availableGeometry()

    def _fit_to_available_screen(self):
        available = self._available_screen_geometry()
        width = min(1450, max(640, int(available.width() * .95)))
        height = min(820, max(480, int(available.height() * .92)))
        width = min(width, available.width())
        height = min(height, available.height())
        self.resize(width, height)
        frame = self.frameGeometry()
        frame.moveCenter(available.center())
        frame.moveLeft(max(available.left(), min(frame.left(), available.right() - frame.width() + 1)))
        frame.moveTop(max(available.top(), min(frame.top(), available.bottom() - frame.height() + 1)))
        self.move(frame.topLeft())

    def _install_page_navigation(self):
        scrollbar = self.page_scroll.verticalScrollBar()
        bindings = (
            (Qt.Key.Key_PageDown, lambda: scrollbar.setValue(
                min(scrollbar.maximum(), scrollbar.value() + scrollbar.pageStep()))),
            (Qt.Key.Key_PageUp, lambda: scrollbar.setValue(
                max(scrollbar.minimum(), scrollbar.value() - scrollbar.pageStep()))),
            (Qt.Key.Key_Home, lambda: scrollbar.setValue(scrollbar.minimum())),
            (Qt.Key.Key_End, lambda: scrollbar.setValue(scrollbar.maximum())),
        )
        self.page_shortcuts = []
        for key, callback in bindings:
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(callback)
            self.page_shortcuts.append(shortcut)

    def _reflow_action_buttons(self):
        width = max(0, self.width() - 40)
        columns = 8 if width >= 1250 else 4 if width >= 780 else 2
        for button in self.action_buttons:
            self.actions_grid.removeWidget(button)
        for index, button in enumerate(self.action_buttons):
            self.actions_grid.addWidget(button, index // columns, index % columns)
        for column in range(8):
            self.actions_grid.setColumnStretch(column, 1 if column < columns else 0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reflow_action_buttons()

    def _load_project_speaker_state(self, project):
        self.working_project = Project.from_dict(project.to_dict())
        self.source_project = self.working_project
        self.speakers = deepcopy(self.working_project.speakers)
        self.speakers.setdefault("SPK_UNKNOWN", Speaker("SPK_UNKNOWN").__dict__.copy())
        self.assignments = {row.id: row.speaker_id for row in self.working_project.utterances}

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
            values = [utterance.id, format_srt_timestamp(utterance.start),
                      format_srt_timestamp(utterance.end),
                      f"{utterance.duration:.3f}", self.original_assignments[utterance.id],
                      speaker_id, overlap, utterance.zh,
                      utterance.speaker_confidence if utterance.speaker_confidence is not None else "Unknown"]
            proposal = self.proposals.get(utterance.id, {})
            values.extend([
                proposal.get("proposed_speaker_id", "SPK_UNKNOWN"),
                " · ".join(filter(None, (proposal.get("character_id", "UNKNOWN"),
                                           proposal.get("semantic_label", "")))),
                proposal.get("addressee_id", "UNKNOWN"),
                proposal.get("confidence", "Unknown"), proposal.get("status", "UNKNOWN"),
                proposal.get("reason", ""),
            ])
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if proposal.get("reason"):
                    item.setToolTip(proposal["reason"])
                colors = {
                    "PROPOSED": "#DDF4E4", "NEED_REVIEW": "#FFF1BF",
                    "CONFLICT": "#FFD6D6", "USER_CONFIRMED": "#D7E9FF",
                    "UNKNOWN": "#E7E7E7",
                }
                if proposal.get("status") in colors:
                    item.setBackground(QBrush(QColor(colors[proposal["status"]])))
                self.table.setItem(row_index, column, item)
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

    def apply_proposals(self):
        """Copy proposals into dialog working state only; Confirm remains authoritative."""
        after_speakers, after_assignments = self._working_state()
        changed = False
        for item in self.proposals.values():
            speaker_id = item.get("proposed_speaker_id")
            utterance_id = item.get("utterance_id")
            if item.get("status") != "PROPOSED" or speaker_id == "SPK_UNKNOWN":
                continue
            if speaker_id not in after_speakers:
                label = item.get("semantic_label") or item.get("character_id") or "Unknown"
                after_speakers[speaker_id] = Speaker(speaker_id, label).__dict__.copy()
            if after_assignments.get(utterance_id) == "SPK_UNKNOWN":
                after_assignments[utterance_id] = speaker_id
                changed = True
        if changed:
            self.undo_stack.push(_WorkingSpeakerCommand(
                self, self._working_state(), (after_speakers, after_assignments),
                "Áp dụng speaker đề xuất",
            ))
        else:
            QMessageBox.information(self, "Speaker", "Không có đề xuất mới đủ tin cậy để áp dụng.")

    def apply_selected_cluster(self):
        selected = self.cluster_table.selectionModel().selectedRows()
        if not selected:
            QMessageBox.warning(self, "Speaker", "Vui lòng chọn một cluster đề xuất.")
            return
        speaker_id = self.cluster_table.item(selected[0].row(), 0).text()
        after_speakers, after_assignments = self._working_state()
        proposal_rows = [item for item in self.proposals.values()
                         if item.get("status") == "PROPOSED"
                         and item.get("proposed_speaker_id") == speaker_id]
        if not proposal_rows:
            return
        sample = proposal_rows[0]
        if speaker_id not in after_speakers:
            label = sample.get("semantic_label") or sample.get("character_id") or "Unknown"
            after_speakers[speaker_id] = Speaker(speaker_id, label).__dict__.copy()
        for item in proposal_rows:
            if after_assignments.get(item["utterance_id"]) == "SPK_UNKNOWN":
                after_assignments[item["utterance_id"]] = speaker_id
        self.undo_stack.push(_WorkingSpeakerCommand(
            self, self._working_state(), (after_speakers, after_assignments),
            f"Áp dụng cluster {speaker_id}",
        ))

    def split_selected(self):
        ids = self.ids()
        if len(ids) != 1:
            QMessageBox.warning(self, "Tách dòng", "Vui lòng chọn đúng một dòng cần tách.")
            return
        utterance = next(row for row in self.working_project.utterances if row.id == ids[0])
        dialog = UtteranceSplitDialog(
            utterance, self._speaker_options(), self.play_range, self,
        )
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        try:
            left, right = split_utterance(
                self.working_project, utterance.id, dialog.boundary.value(),
                dialog.timestamp.value(), dialog.left_speaker.currentData(),
                dialog.right_speaker.currentData(),
            )
        except ValueError as exc:
            QMessageBox.warning(self, "Tách dòng", str(exc))
            return
        self.speakers = deepcopy(self.working_project.speakers)
        self.assignments.pop(utterance.id, None)
        self.assignments[left.id] = left.speaker_id
        self.assignments[right.id] = right.speaker_id
        self.original_assignments[left.id] = left.speaker_id
        self.original_assignments[right.id] = right.speaker_id
        self.proposals.pop(utterance.id, None)
        self.undo_stack.clear()
        self.refresh()

    def approve(self):
        self.stop_playback()
        if any(speaker_id == "SPK_UNKNOWN" for speaker_id in self.assignments.values()):
            answer = QMessageBox.question(
                self, "Xác nhận speaker chưa rõ",
                "Một số dòng vẫn là SPK_UNKNOWN. Bạn có muốn đánh dấu chúng là đã duyệt nhưng chưa xác định không?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
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
        self.play_range(utterance.start, utterance.end)

    def play_range(self, start, end):
        self.stop_playback()
        source = self.directory / "audio" / "source.wav"
        if not source.exists(): source = Path(self.source_project.source_video_path)
        if not source.exists():
            QMessageBox.warning(self, "Audio", "Không tìm thấy audio/video nguồn"); return
        self.start_ms = round(max(0.0, start - .25) * 1000)
        self.end_ms = round((end + .25) * 1000)
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
