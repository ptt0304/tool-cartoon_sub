import json
import os
import tempfile
import unittest
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QInputDialog, QLabel, QMessageBox, QPushButton, QScrollArea

from cartoon_sub.project.project_manager import ProjectManager
from cartoon_sub.speaker.models import Speaker
from cartoon_sub.speaker.service import (
    apply_speaker_review_state,
    capture_initial_speaker_state,
    restore_initial_speaker_state,
)
from cartoon_sub.subtitle.export_service import export_current_srt
from cartoon_sub.subtitle.models import Project, Segment
from cartoon_sub.subtitle.parser import import_srt
from cartoon_sub.ui.speaker_dialog import SpeakerDialog, UtteranceSplitDialog, next_speaker_id


def speaker_registry(*numbers):
    return {f"SPK_{number:02d}": asdict(Speaker(f"SPK_{number:02d}")) for number in numbers}


def sample_project():
    rows = [
        Segment(1, 0.0, 1.0, "第一句", speaker_id="SPK_01", speaker_confidence=0.91),
        Segment(2, 1.2, 2.2, "第二句", speaker_id="SPK_01", speaker_confidence=0.72),
        Segment(3, 2.4, 3.4, "第三句", speaker_id="SPK_02", speaker_confidence=0.83),
    ]
    project = Project("speaker-review", "missing.mp4", segments=rows)
    project.speakers = speaker_registry(1, 2, 3, 4, 5)
    capture_initial_speaker_state(project, replace=True)
    return project


class SpeakerReviewSessionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_next_id_uses_max_and_empty_speaker_survives_reload(self):
        project = sample_project()
        self.assertEqual(next_speaker_id(project.speakers), "SPK_06")
        edited = deepcopy(project.speakers)
        edited["SPK_06"] = asdict(Speaker("SPK_06", "Narrator"))
        apply_speaker_review_state(project, edited, {row.id: row.speaker_id for row in project.utterances})
        with tempfile.TemporaryDirectory() as directory:
            manager = ProjectManager()
            manager.save(project, directory)
            loaded = manager.load(Path(directory) / "project.json")
        self.assertIn("SPK_06", loaded.speakers)
        self.assertEqual(sum(row.speaker_id == "SPK_06" for row in loaded.utterances), 0)

    def test_rename_keeps_stable_id_and_ai_confidence(self):
        project = sample_project()
        confidence = [row.speaker_confidence for row in project.utterances]
        edited = deepcopy(project.speakers)
        edited["SPK_01"]["name"] = "Nhân vật chính"
        apply_speaker_review_state(project, edited, {row.id: row.speaker_id for row in project.utterances})
        self.assertEqual(project.speakers["SPK_01"]["name"], "Nhân vật chính")
        self.assertEqual([row.speaker_id for row in project.utterances], ["SPK_01", "SPK_01", "SPK_02"])
        self.assertEqual([row.speaker_confidence for row in project.utterances], confidence)

    def test_filter_select_all_batch_assign_and_undo_are_working_only(self):
        project = sample_project()
        dialog = SpeakerDialog(project, ".")
        canonical = deepcopy(project.to_dict())
        dialog.filter.setCurrentIndex(dialog.filter.findData("SPK_01"))
        dialog.select_visible()
        def choose_two(_parent, _title, _prompt, items, *_args):
            self.assertFalse(any(item.startswith("SPK_01 ·") for item in items))
            return next(item for item in items if item.startswith("SPK_02 ·")), True
        with patch.object(QInputDialog, "getItem", side_effect=choose_two):
            dialog.assign()
        self.assertEqual(dialog.assignments, {1: "SPK_02", 2: "SPK_02", 3: "SPK_02"})
        dialog.undo_stack.undo()
        self.assertEqual(dialog.assignments, {1: "SPK_01", 2: "SPK_01", 3: "SPK_02"})
        dialog.add()
        self.assertIn("SPK_06", dialog.speakers)
        self.assertIn("(0 câu)", dialog.filter.itemText(dialog.filter.findData("SPK_06")))
        dialog.filter.setCurrentIndex(0)
        dialog.select_visible()
        with patch.object(QInputDialog, "getItem", side_effect=lambda *args:
                          (next(item for item in args[3] if item.startswith("SPK_06 ·")), True)):
            dialog.assign()
        self.assertEqual(set(dialog.assignments.values()), {"SPK_06"})
        self.assertIn("(3 câu)", dialog.filter.itemText(dialog.filter.findData("SPK_06")))
        dialog.undo_stack.undo()
        dialog.undo_stack.undo()
        self.assertNotIn("SPK_06", dialog.speakers)
        self.assertEqual(project.to_dict(), canonical)
        dialog.reject()

    def test_only_required_actions_are_exposed(self):
        dialog = SpeakerDialog(sample_project(), ".")
        labels = {button.text() for button in dialog.findChildren(QPushButton)}
        self.assertTrue({
            "Chọn tất cả", "Speaker mới", "Đổi tên", "Gán dòng cho SPK",
            "Nghe dòng chọn", "Reset", "Xác nhận Speaker và Lưu", "Hủy",
        }.issubset(labels))
        self.assertIn("Tách dòng", labels)
        self.assertTrue({"Tách dòng chọn", "Gộp vào…", "Lưu chỉnh sửa chưa xác nhận"}.isdisjoint(labels))
        self.assertNotIn("Speaker thao tác", {label.text() for label in dialog.findChildren(QLabel)})
        self.assertIn("Gợi ý speaker từ STT", dialog.capability_status.text())
        self.assertFalse(hasattr(dialog, "target"))
        dialog.reject()

    def test_responsive_outer_page_scroll_geometry_and_navigation(self):
        dialog = SpeakerDialog(sample_project(), ".")
        dialog.show()
        self.app.processEvents()
        available = dialog._available_screen_geometry()
        frame = dialog.frameGeometry()
        self.assertTrue(available.contains(frame.topLeft()))
        self.assertTrue(available.contains(frame.bottomRight()))
        self.assertIsInstance(dialog.page_scroll, QScrollArea)
        self.assertTrue(dialog.page_scroll.widgetResizable())
        self.assertEqual(dialog.page_scroll.horizontalScrollBarPolicy(),
                         Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.assertFalse(dialog.page_content.isAncestorOf(dialog.confirm_button))

        dialog.resize(min(700, available.width()), min(430, available.height()))
        self.app.processEvents()
        bar = dialog.page_scroll.verticalScrollBar()
        self.assertGreater(bar.maximum(), 0)
        dialog.table.setFocus()
        QTest.keyClick(dialog.table, Qt.Key.Key_End)
        self.app.processEvents()
        self.assertEqual(bar.value(), bar.maximum())
        QTest.keyClick(dialog.table, Qt.Key.Key_Home)
        self.app.processEvents()
        self.assertEqual(bar.value(), bar.minimum())
        QTest.keyClick(dialog.table, Qt.Key.Key_PageDown)
        self.app.processEvents()
        self.assertGreater(bar.value(), bar.minimum())
        previous = bar.value()
        QTest.keyClick(dialog.table, Qt.Key.Key_PageUp)
        self.app.processEvents()
        self.assertLess(bar.value(), previous)
        wheel = QWheelEvent(
            QPointF(10, 10), QPointF(10, 10), QPoint(), QPoint(0, -120),
            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.ScrollUpdate, False,
        )
        QApplication.sendEvent(dialog.page_scroll.viewport(), wheel)
        self.app.processEvents()
        self.assertGreater(bar.value(), bar.minimum())
        positions = [dialog.actions_grid.getItemPosition(dialog.actions_grid.indexOf(button))
                     for button in dialog.action_buttons]
        self.assertLessEqual(max(position[1] for position in positions), 1)
        self.assertGreater(max(position[0] for position in positions), 0)
        self.assertTrue(dialog.confirm_button.isVisible())
        self.assertGreaterEqual(dialog.table.horizontalScrollBar().maximum(), 0)
        dialog.reject()

    def test_split_dialog_exposes_text_time_speakers_and_audio_preview(self):
        project = sample_project()
        preview = Mock()
        options = [(speaker_id, speaker_id) for speaker_id in project.speakers]
        dialog = UtteranceSplitDialog(project.utterances[0], options, preview)
        dialog.boundary.setValue(1)
        self.assertEqual(dialog.left_preview.text() + dialog.right_preview.text(), "第一句")
        self.assertLess(project.utterances[0].start, dialog.timestamp.value())
        self.assertLess(dialog.timestamp.value(), project.utterances[0].end)
        self.assertGreater(dialog.left_speaker.count(), 0)
        button = next(item for item in dialog.findChildren(QPushButton)
                      if item.text() == "Nghe audio dòng gốc")
        button.click()
        preview.assert_called_once_with(0.0, 1.0)
        dialog.reject()

    def test_original_column_stays_immutable_across_two_assignments(self):
        dialog = SpeakerDialog(sample_project(), ".")
        self.assertEqual(dialog.table.horizontalHeaderItem(4).text(), "Speaker original")
        self.assertEqual(dialog.table.horizontalHeaderItem(5).text(), "Speaker")
        dialog.table.selectRow(0)
        with patch.object(QInputDialog, "getItem", side_effect=lambda *args:
                          (next(item for item in args[3] if item.startswith("SPK_02 ·")), True)):
            dialog.assign()
        self.assertEqual((dialog.table.item(0, 4).text(), dialog.table.item(0, 5).text()),
                         ("SPK_01", "SPK_02"))
        dialog.table.selectRow(0)
        with patch.object(QInputDialog, "getItem", side_effect=lambda *args:
                          (next(item for item in args[3] if item.startswith("SPK_03 ·")), True)):
            dialog.assign()
        self.assertEqual((dialog.table.item(0, 4).text(), dialog.table.item(0, 5).text()),
                         ("SPK_01", "SPK_03"))
        dialog.reject()

    def test_mixed_selection_assigns_all_and_keeps_each_original(self):
        dialog = SpeakerDialog(sample_project(), ".")
        dialog.select_visible()
        def choose_three(_parent, _title, _prompt, items, *_args):
            self.assertTrue(any(item.startswith("SPK_01 ·") for item in items))
            self.assertTrue(any(item.startswith("SPK_02 ·") for item in items))
            return next(item for item in items if item.startswith("SPK_03 ·")), True
        with patch.object(QInputDialog, "getItem", side_effect=choose_three):
            dialog.assign()
        self.assertEqual(set(dialog.assignments.values()), {"SPK_03"})
        self.assertEqual([dialog.table.item(row, 4).text() for row in range(3)],
                         ["SPK_01", "SPK_01", "SPK_02"])
        dialog.reject()

    def test_no_selection_warns_without_opening_assign_popup(self):
        dialog = SpeakerDialog(sample_project(), ".")
        with patch.object(QMessageBox, "warning") as warning, \
                patch.object(QInputDialog, "getItem") as chooser:
            dialog.assign()
        warning.assert_called_once_with(
            dialog, "Speaker", "Vui lòng chọn ít nhất một dòng cần gán speaker.",
        )
        chooser.assert_not_called()
        dialog.reject()

    def test_rename_preserves_original_assignments(self):
        dialog = SpeakerDialog(sample_project(), ".")
        originals = dict(dialog.original_assignments)
        with patch.object(QInputDialog, "getItem", side_effect=lambda *args:
                          (next(item for item in args[3] if item.startswith("SPK_01 ·")), True)), \
                patch.object(QInputDialog, "getText", return_value=("Nam chính", True)):
            dialog.rename()
        self.assertEqual(dialog.original_assignments, originals)
        self.assertEqual([dialog.table.item(row, 4).text() for row in range(3)],
                         ["SPK_01", "SPK_01", "SPK_02"])
        dialog.reject()

    def test_multiple_confirms_then_reset_restores_exact_initial_speaker_state(self):
        project = sample_project()
        baseline = deepcopy(project.speaker_review_initial_state)
        original_text_timing = [(row.zh, row.start, row.end) for row in project.utterances]
        first = deepcopy(project.speakers)
        first["SPK_01"]["name"] = "A"
        first["SPK_06"] = asdict(Speaker("SPK_06", "B"))
        apply_speaker_review_state(project, first, {1: "SPK_06", 2: "SPK_01", 3: "SPK_02"})
        second = deepcopy(project.speakers)
        second["SPK_06"]["name"] = "B mới"
        apply_speaker_review_state(project, second, {1: "SPK_06", 2: "SPK_06", 3: "SPK_02"})
        restore_initial_speaker_state(project)
        self.assertEqual(project.speakers, baseline["speakers"])
        self.assertEqual({str(row.id): row.speaker_id for row in project.utterances}, baseline["assignments"])
        self.assertNotIn("SPK_06", project.speakers)
        self.assertEqual([(row.zh, row.start, row.end) for row in project.utterances], original_text_timing)

    def test_dialog_reset_commits_immediately_and_refreshes_working_state(self):
        project = sample_project()
        edited = deepcopy(project.speakers)
        edited["SPK_06"] = asdict(Speaker("SPK_06"))
        apply_speaker_review_state(project, edited, {1: "SPK_06", 2: "SPK_01", 3: "SPK_02"})
        dialog = SpeakerDialog(project, ".", reset_callback=lambda: restore_initial_speaker_state(project))
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            dialog.reset_to_initial()
        self.assertNotIn("SPK_06", project.speakers)
        self.assertEqual(dialog.assignments[1], "SPK_01")
        dialog.reject()

    def test_old_project_migrates_current_state_once_as_baseline(self):
        project = sample_project()
        project.speaker_review_initial_state = {}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "project.json"
            raw = project.to_dict()
            raw.pop("speaker_review_initial_state", None)
            path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
            manager = ProjectManager()
            loaded = manager.load(path)
            baseline = deepcopy(loaded.speaker_review_initial_state)
            loaded.speakers["SPK_01"]["name"] = "Sau migration"
            manager.save(loaded, directory)
            reloaded = manager.load(path)
        self.assertTrue(baseline)
        self.assertEqual(reloaded.speaker_review_initial_state, baseline)

    def test_transcript_export_after_confirm_and_reset_is_versioned_current_snapshot(self):
        project = sample_project()
        edited = deepcopy(project.speakers)
        edited["SPK_01"]["name"] = "Đã xác nhận"
        apply_speaker_review_state(project, edited, {1: "SPK_02", 2: "SPK_01", 3: "SPK_02"})
        with tempfile.TemporaryDirectory() as directory:
            first = export_current_srt(project, directory, "transcript")
            restore_initial_speaker_state(project)
            second = export_current_srt(project, directory, "transcript")
            self.assertEqual((first.name, second.name), ("transcript_0.srt", "transcript_1.srt"))
            self.assertEqual([row.zh for row in import_srt(second)], ["第一句", "第二句", "第三句"])
            self.assertTrue(first.exists())


if __name__ == "__main__":
    unittest.main()
