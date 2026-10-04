import unittest

from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

from cartoon_sub.subtitle.models import Project, Segment
from cartoon_sub.ui.table_search import add_table_search
from cartoon_sub.ui.timeline_table import create_table, install_delta_target_filter, populate


class DeltaTargetFilterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.host = QWidget(); layout = QVBoxLayout(self.host)
        self.table = create_table()
        self.search, _ = add_table_search(layout, self.table, (0, 4, 5, 7, 8))
        self.minimum, self.maximum = install_delta_target_filter(self.table)
        rows = [
            Segment(1, 0, 1, "甲", vi="", target_override=3, speaker_name="Mèo"),       # -3
            Segment(2, 1, 2, "乙", vi="một hai ba", target_override=3, speaker_name="Cô chủ"),  # 0
            Segment(3, 2, 3, "丙", vi="một hai ba bốn năm sáu", target_override=3, speaker_name="Cô chủ"),  # +3
            Segment(4, 3, 4, "丁", vi="một hai ba bốn năm sáu bảy tám chín mười", target_override=3, speaker_name="Mèo"),  # +7
        ]
        populate(self.table, Project("p", "source.mp4", segments=rows))

    def visible_ids(self):
        return [int(self.table.item(row, 0).text()) for row in range(self.table.rowCount())
                if not self.table.isRowHidden(row)]

    def test_empty_inclusive_and_one_sided_ranges(self):
        self.assertEqual(self.visible_ids(), [1, 2, 3, 4])
        self.minimum.setText("-3"); self.maximum.setText("+3")
        self.assertEqual(self.visible_ids(), [1, 2, 3])
        self.minimum.setText("+3"); self.maximum.clear()
        self.assertEqual(self.visible_ids(), [3, 4])
        self.minimum.clear(); self.maximum.setText("0")
        self.assertEqual(self.visible_ids(), [1, 2])
        self.minimum.setText("0")
        self.assertEqual(self.visible_ids(), [2])

    def test_combines_with_search_and_invalid_range_matches_none(self):
        self.minimum.setText("0"); self.maximum.setText("+3")
        self.search.setText("cô chủ")
        self.assertEqual(self.visible_ids(), [2, 3])
        self.minimum.setText("4"); self.maximum.setText("2")
        self.assertEqual(self.visible_ids(), [])


if __name__ == "__main__":
    unittest.main()
