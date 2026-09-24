import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from cartoon_sub.subtitle.models import Project
from cartoon_sub.ui.tabs.audio_tab import AudioPage


_app = QApplication.instance() or QApplication([])


def project_with_mappings():
    return Project("voices", "source.mp4", speakers={
        "SPK_01": {"id": "SPK_01", "name": "One", "tts_voice_id": "voice_a", "tts_speed": 1.0},
        "SPK_02": {"id": "SPK_02", "name": "Two", "tts_voice_id": "voice_b", "tts_speed": 1.0},
        "SPK_03": {"id": "SPK_03", "name": "Three", "tts_voice_id": None, "tts_speed": 1.0},
    })


def voices(*, a=True, b=False, c=True):
    return [
        {"voice_id": "voice_a", "display_name": "A", "status": "READY", "engine": "vieneu", "favorite": a},
        {"voice_id": "voice_b", "display_name": "B", "status": "READY", "engine": "piper", "favorite": b},
        {"voice_id": "voice_c", "display_name": "C", "status": "READY", "engine": "vieneu", "favorite": c},
    ]


def combo_ids(combo):
    return [combo.itemData(index) for index in range(combo.count()) if combo.itemData(index)]


class AudioPageVoiceTests(unittest.TestCase):
    def test_favorites_and_all_sections_are_shared_by_rows_and_batch(self):
        page = AudioPage()
        page.populate(project_with_mappings(), voices())
        row = page.tts_table.cellWidget(2, 3)
        expected = ["voice_a", "voice_c", "voice_a", "voice_b", "voice_c"]
        self.assertEqual(combo_ids(row), expected)
        self.assertEqual(combo_ids(page.batch_voice), expected)
        self.assertEqual([row.itemText(index) for index in range(row.count())],
            ["— Chưa chọn —", "★ Giọng yêu thích", "A", "C", "Tất cả giọng", "A", "B", "C"])

    def test_nonfavorite_current_mapping_survives_filter_without_signal(self):
        project = project_with_mappings()
        page = AudioPage()
        changes = []
        page.mapping_changed.connect(lambda *args: changes.append(args))
        page.populate(project, voices())
        combo = page.tts_table.cellWidget(1, 3)
        self.assertEqual(combo.currentData(), "voice_b")
        page.populate(project, voices(a=False, b=False, c=True))
        combo = page.tts_table.cellWidget(1, 3)
        self.assertEqual(combo.currentData(), "voice_b")
        self.assertEqual(project.speakers["SPK_02"]["tts_voice_id"], "voice_b")
        self.assertEqual(changes, [])

    def test_preview_and_batch_emit_canonical_voice_ids(self):
        page = AudioPage()
        page.populate(project_with_mappings(), voices())
        previewed = []
        page.preview_requested.connect(previewed.append)
        row_combo = page.tts_table.cellWidget(2, 3)
        row_combo.setCurrentIndex(row_combo.findData("voice_c"))
        page.tts_table.setCurrentCell(2, 0)
        page._on_preview_clicked()
        self.assertEqual(previewed, ["voice_c"])

        assigned = []
        page.batch_voice_requested.connect(lambda ids, voice_id: assigned.append((ids, voice_id)))
        page._set_all_checked(True)
        page.batch_voice.setCurrentIndex(page.batch_voice.findData("voice_a"))
        page._apply_batch_voice()
        self.assertEqual(assigned, [(["SPK_01", "SPK_02", "SPK_03"], "voice_a")])

    def test_refresh_replaces_favorite_metadata_without_restart(self):
        page = AudioPage()
        project = project_with_mappings()
        page.populate(project, voices(a=True, b=False, c=False))
        self.assertEqual(combo_ids(page.batch_voice)[:1], ["voice_a"])
        page.populate(project, voices(a=False, b=True, c=True))
        self.assertEqual(combo_ids(page.batch_voice)[:2], ["voice_b", "voice_c"])

    def test_empty_favorites_is_safe_and_does_not_change_mappings(self):
        project = project_with_mappings()
        page = AudioPage()
        page.populate(project, voices(a=False, b=False, c=False))
        self.assertEqual(combo_ids(page.batch_voice), ["voice_a", "voice_b", "voice_c"])
        header = page.batch_voice.model().item(1)
        empty = page.batch_voice.model().item(2)
        self.assertEqual(header.text(), "★ Giọng yêu thích")
        self.assertEqual(empty.text(), "Không có giọng yêu thích")
        self.assertFalse(bool(empty.flags() & Qt.ItemFlag.ItemIsEnabled))
        self.assertEqual(page.tts_table.cellWidget(1, 3).currentData(), "voice_b")

    def test_offline_refresh_path_preserves_cached_list_and_mapping(self):
        project = project_with_mappings()
        page = AudioPage()
        page.populate(project, voices())
        page.populate(project, None)
        self.assertEqual(combo_ids(page.batch_voice),
                         ["voice_a", "voice_c", "voice_a", "voice_b", "voice_c"])
        self.assertEqual(project.speakers["SPK_02"]["tts_voice_id"], "voice_b")

    def test_deleted_voice_disappears_and_selector_falls_back(self):
        project = project_with_mappings()
        page = AudioPage()
        page.populate(project, voices(a=True, b=True, c=True))
        remaining = [voice for voice in voices(a=True, b=True, c=True) if voice["voice_id"] != "voice_b"]
        page.populate(project, remaining)
        combo = page.tts_table.cellWidget(1, 3)
        self.assertEqual(combo.currentData(), "voice_a")
        self.assertNotIn("voice_b", combo_ids(page.batch_voice))


if __name__ == "__main__":
    unittest.main()
