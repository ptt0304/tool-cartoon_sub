import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from unittest.mock import Mock
from PySide6.QtWidgets import QApplication
from PySide6.QtMultimedia import QMediaPlayer
from cartoon_sub.ui.speaker_dialog import SpeakerDialog
from cartoon_sub.subtitle.models import Project, Segment


class SpeakerPlaybackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        project = Project('test', 'missing.mp4', segments=[
            Segment(1, 1, 3, '你好', speaker_id='SPK_01')])
        self.dialog = SpeakerDialog(project, '.')
        self.dialog.player = Mock()
        self.dialog.timer = Mock()
        self.dialog.start_ms = 1000
        self.dialog.end_ms = 3000
        self.dialog.play_pending = True

    def tearDown(self):
        self.dialog.reject()
        self.dialog.deleteLater()
        self.app.processEvents()

    def test_loaded_event_consumes_one_explicit_play_request(self):
        d = self.dialog
        d.loaded(QMediaPlayer.MediaStatus.LoadedMedia)
        d.loaded(QMediaPlayer.MediaStatus.BufferedMedia)
        d.loaded(QMediaPlayer.MediaStatus.LoadedMedia)
        d.player.play.assert_called_once()
        d.player.setPosition.assert_called_once_with(1000)

    def test_confirm_and_cancel_do_not_restart_on_stop_or_late_load(self):
        d = self.dialog
        d.player.stop.side_effect = lambda: d.loaded(QMediaPlayer.MediaStatus.LoadedMedia)
        d.approve()
        d.loaded(QMediaPlayer.MediaStatus.LoadedMedia)
        d.player.play.assert_not_called()
        self.assertFalse(d.play_pending)
        self.assertEqual(d.end_ms, 0)
        d.play_pending = True
        d.end_ms = 3000
        d.reject()
        d.loaded(QMediaPlayer.MediaStatus.LoadedMedia)
        d.player.play.assert_not_called()

    def test_clip_end_stops_without_restarting(self):
        d = self.dialog
        d.loaded(QMediaPlayer.MediaStatus.LoadedMedia)
        d.player.position.return_value = 3000
        d.player.stop.side_effect = lambda: d.loaded(QMediaPlayer.MediaStatus.LoadedMedia)
        d.check_playback()
        d.player.play.assert_called_once()
        d.player.stop.assert_called_once()
        self.assertEqual(d.end_ms, 0)

    def test_hiding_dialog_cancels_pending_audio(self):
        d = self.dialog
        d.show()
        self.app.processEvents()
        d.hide()
        d.loaded(QMediaPlayer.MediaStatus.LoadedMedia)
        d.player.play.assert_not_called()
        self.assertFalse(d.play_pending)
