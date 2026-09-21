import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from cartoon_sub.subtitle.models import Project
from cartoon_sub.ui.tabs.audio_tab import AudioPage


_app = QApplication.instance() or QApplication([])


class AudioPageVoiceTests(unittest.TestCase):
    def test_preview_uses_current_selected_row_combo_voice_id_and_favorites_group(self):
        page = AudioPage()
        project = Project("voices", "source.mp4", speakers={
            "SPK_01": {"id": "SPK_01", "name": "One", "tts_voice_id": "voice_a", "tts_speed": 1.0},
            "SPK_02": {"id": "SPK_02", "name": "Two", "tts_voice_id": "voice_b", "tts_speed": 1.0},
        })
        voices = [
            {"voice_id": "voice_a", "display_name": "A", "status": "READY", "favorite": True},
            {"voice_id": "voice_b", "display_name": "B", "status": "READY", "favorite": False},
            {"voice_id": "voice_c", "display_name": "C", "status": "READY", "favorite": True},
        ]
        page.populate(project, voices)
        combo = page.tts_table.cellWidget(1, 3)
        self.assertEqual([combo.itemText(i) for i in range(combo.count())],
            ["— Chưa chọn —", "★ Giọng yêu thích", "A", "C", "Tất cả giọng", "A", "B", "C"])
        self.assertFalse(bool(combo.model().item(1).flags() & Qt.ItemFlag.ItemIsEnabled))
        page.tts_table.setCurrentCell(1, 0)
        combo.setCurrentIndex(combo.findData("voice_c"))
        requested = []
        page.preview_requested.connect(requested.append)
        page._on_preview_clicked()
        self.assertEqual(requested, ["voice_c"])


if __name__ == "__main__":
    unittest.main()
