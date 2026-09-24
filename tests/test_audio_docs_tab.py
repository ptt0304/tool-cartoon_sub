import os
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import QApplication, QTextBrowser

from cartoon_sub.ui.docs_dialog import DocsWindow, TOPICS


class AudioDocsTabTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_audio_tab_order_content_scroll_and_open(self):
        self.assertEqual([title for title, _ in TOPICS][7:],
            ['8. Audio', '9. Export', '10. Lỗi thường gặp'])
        audio_html = TOPICS[7][1]
        for expected in ('Local_TTS URL', 'Preview selected voice', 'Generate / Resume TTS',
                         'Build Dubbed Audio', 'Original Audio Volume', 'Build Final Audio',
                         '★ Giọng yêu thích', 'Tất cả giọng', 'Test connection',
                         'audio/tts/dubbed_mix.wav', 'audio/final_audio.wav', 'Troubleshooting'):
            self.assertIn(expected, audio_html)

        dialog = DocsWindow();dialog.resize(500, 260);dialog.show();self.app.processEvents()
        self.assertEqual(dialog.tabs.tabText(7), '8. Audio')
        browser = dialog.tabs.widget(7)
        self.assertIsInstance(browser, QTextBrowser)
        dialog.tabs.setCurrentIndex(7);self.app.processEvents()
        self.assertGreater(browser.verticalScrollBar().maximum(), 0)
        dialog.open_topic(7);self.app.processEvents()
        self.assertEqual(len(dialog.topic_windows), 1)
        self.assertIn('8. Audio', dialog.topic_windows[0].windowTitle())
        dialog.topic_windows[0].close();dialog.close()


if __name__ == '__main__':
    unittest.main()
