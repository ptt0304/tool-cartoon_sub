import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QTableWidgetItem

from cartoon_sub.subtitle.models import DisplaySegment, Project, Utterance
from cartoon_sub.ui.tabs.subtitle_tab import build as build_subtitle_tab
from cartoon_sub.ui.tabs.transcript_tab import build as build_transcript_tab
from cartoon_sub.ui.tabs.translate_tab import build as build_translate_tab
from cartoon_sub.ui.timeline_table import populate


class TableSearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _project(self):
        first = Utterance(1, 511.065, 512.205, "我给你画伏底阵。", vi="Ta vẽ phù đáy trận cho ngươi.", speaker_id="SPK_01")
        first.set_display_segments([
            DisplaySegment("1.1", 1, 511.065, 512.205, "Ta vẽ phù đáy trận cho ngươi.")
        ])
        second = Utterance(2, 520.0, 521.0, "再见", vi="Tạm biệt", speaker_id="SPK_02")
        return Project("search", "missing.mp4", segments=[first, second])

    def test_transcript_search_chinese_and_clear(self):
        page = build_transcript_tab()
        page.table.setRowCount(2)
        for row, values in enumerate((("1", "SPK_01", "我给你画伏底阵。"), ("2", "SPK_02", "再见"))):
            for column, value in zip((0, 4, 6), values):
                page.table.setItem(row, column, QTableWidgetItem(value))
        page.search_edit.setText("伏底阵")
        self.assertFalse(page.table.isRowHidden(0))
        self.assertTrue(page.table.isRowHidden(1))
        page.search_clear.click()
        self.assertFalse(page.table.isRowHidden(1))

    def test_translate_search_vietnamese_and_timestamp_display(self):
        page = build_translate_tab()
        populate(page.table, self._project())
        self.assertEqual(page.table.item(0, 1).text(), "00:08:31,065")
        page.search_edit.setText("PHÙ ĐÁY")
        self.assertFalse(page.table.isRowHidden(0))
        self.assertTrue(page.table.isRowHidden(1))

    def test_subtitle_child_search_keeps_parent_and_hides_nonmatch(self):
        page = build_subtitle_tab()
        page.load_project(self._project())
        page.search_edit.setText("phù đáy")
        self.assertFalse(page.tree.topLevelItem(0).isHidden())
        self.assertFalse(page.tree.topLevelItem(0).child(0).isHidden())
        self.assertTrue(page.tree.topLevelItem(1).isHidden())
        self.assertEqual(page.tree.topLevelItem(0).text(2), "00:08:31,065")
        page.search_clear.click()
        self.assertFalse(page.tree.topLevelItem(1).isHidden())


if __name__ == "__main__":
    unittest.main()
