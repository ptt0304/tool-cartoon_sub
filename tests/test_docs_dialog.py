import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QTextBrowser

from cartoon_sub.ui.docs_dialog import DocsWindow, TOPICS


class DocsDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_all_current_topics_render_and_open_detached_windows(self):
        self.assertEqual([title for title, _ in TOPICS], [
            "1. Giới thiệu", "2. Project", "3. Settings", "4. Transcript",
            "5. Translate", "6. Subtitle", "7. Mask", "8. Audio", "9. Export",
            "10. Lỗi thường gặp", "11. Quy trình",
        ])
        self.assertEqual(TOPICS[-1][1], "<p>Phần này sẽ được bổ sung sau.</p>")
        window = DocsWindow()
        window.show()
        self.app.processEvents()
        for index, (_title, html) in enumerate(TOPICS):
            browser = window.tabs.widget(index)
            self.assertIsInstance(browser, QTextBrowser)
            self.assertTrue(browser.document().toPlainText().strip())
            self.assertEqual(browser.document().toHtml().count("<html"), 1)
            window.open_topic(index)
            self.app.processEvents()
        self.assertEqual(len(window.topic_windows), len(TOPICS))
        for topic in window.topic_windows:
            topic.close()
        window.close()


if __name__ == "__main__":
    unittest.main()
