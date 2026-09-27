import unittest

from PySide6.QtWidgets import QApplication

from cartoon_sub.ui.main_window import MainWindow


_app = QApplication.instance() or QApplication([])


class ManualQAUITests(unittest.TestCase):
    def test_button_exists_and_no_selection_does_not_start_worker(self):
        window = MainWindow()
        try:
            self.assertEqual(window.pages[2].manual_qa_button.text(), "QA/QC AI dòng đã chọn")
            window.qa_selected_translation()
            self.assertIn("Vui lòng chọn ít nhất một dòng", window.statusBar().currentMessage())
            self.assertIsNone(window.worker)
        finally:
            window.voice_sync_timer.stop()
            window.hide()
            window.deleteLater()
            _app.processEvents()


if __name__ == "__main__":
    unittest.main()
