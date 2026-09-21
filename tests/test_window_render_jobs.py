import os
import unittest
from threading import Event
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtWidgets import QApplication

from cartoon_sub.ui.main_window import MainWindow
from cartoon_sub.app.controller import Controller
from cartoon_sub.subtitle.models import Project


class FakeWorker(QObject):
    result = Signal(object)
    error = Signal(object)
    progress = Signal(str)
    finished = Signal()

    def __init__(self, operation, parent=None):
        super().__init__(parent);self.operation=operation;self.cancel_event=Event();self.started=False
    def start(self): self.started=True
    def cancel(self): self.cancel_event.set()


class WindowRenderJobTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_native_maximize_and_restore_down(self):
        window = MainWindow();window.showNormal();self.app.processEvents()
        flags = window.windowFlags()
        self.assertTrue(flags & Qt.WindowType.WindowMinimizeButtonHint)
        self.assertTrue(flags & Qt.WindowType.WindowMaximizeButtonHint)
        self.assertTrue(flags & Qt.WindowType.WindowCloseButtonHint)
        self.assertGreater(window.maximumWidth(), window.screen().availableGeometry().width())
        window.showMaximized();self.app.processEvents()
        self.assertTrue(window.isMaximized())
        window.showNormal();self.app.processEvents()
        self.assertFalse(window.isMaximized())
        window.close()

    def test_tabs_remain_navigable_and_double_start_is_rejected(self):
        with patch('cartoon_sub.ui.main_window.Worker', FakeWorker):
            window = MainWindow();window.show();self.app.processEvents()
            self.assertTrue(window.start_job(lambda **_: 'first', lambda _: None))
            first_worker = window.worker
            self.assertTrue(window.tabs.isEnabled())
            self.assertTrue(window.menuBar().isEnabled())
            window.tabs.setCurrentIndex(5)
            self.assertEqual(window.tabs.currentIndex(), 5)
            self.assertFalse(window.start_job(lambda **_: 'second', lambda _: None))
            self.assertIs(window.worker, first_worker)
            first_worker.finished.emit();self.app.processEvents()
            self.assertIsNone(window.worker)
            window.close()

    def test_export_render_uses_supplied_project_snapshot(self):
        controller = Controller();controller.directory = Path('.')
        controller.project = Project('live', 'live.mp4')
        snapshot = Project('snapshot', 'snapshot.mp4')
        with patch('cartoon_sub.media.preview.VideoRenderer.render', return_value=Path('done.mp4')) as render:
            controller.render_export(test_mode=True, project_snapshot=snapshot)
        self.assertIs(render.call_args.args[0], snapshot)
        self.assertIsNot(render.call_args.args[0], controller.project)


if __name__ == '__main__':
    unittest.main()
