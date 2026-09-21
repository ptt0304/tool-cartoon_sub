import os
import struct
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QAbstractSpinBox, QSlider, QSpinBox

from cartoon_sub.project.project_manager import ProjectManager
from cartoon_sub.subtitle.models import AudioSettings, Project
from cartoon_sub.tts.final_mix_service import FinalAudioMixService
from cartoon_sub.ui.no_wheel import NoWheelNumericFilter
from cartoon_sub.ui.tabs.audio_tab import AudioPage


def write_wav(path, seconds=1):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), 'wb') as writer:
        writer.setnchannels(1);writer.setsampwidth(2);writer.setframerate(8000)
        writer.writeframes(struct.pack('<h', 1000) * 8000 * seconds)


class AudioVolumeInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_three_keyboard_spinboxes_range_and_wheel_disabled(self):
        page = AudioPage();page.resize(800, 600);page.show()
        filt = NoWheelNumericFilter(page);self.app.installEventFilter(filt);self.app.processEvents()
        controls = (page.orig_volume, page.dub_volume, page.add_volume)
        self.assertFalse(page.findChildren(QSlider))
        for spin in controls:
            self.assertIsInstance(spin, QSpinBox)
            self.assertEqual((spin.minimum(), spin.maximum()), (0, 100))
            self.assertEqual(spin.buttonSymbols(), QAbstractSpinBox.ButtonSymbols.NoButtons)
            spin.lineEdit().selectAll();QTest.keyClicks(spin, '75');QTest.keyClick(spin, Qt.Key.Key_Return)
            self.assertEqual(spin.value(), 75)
            event = QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(), QPoint(0, 120),
                Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.ScrollUpdate, False)
            QApplication.sendEvent(spin, event)
            self.assertEqual(spin.value(), 75)
        self.app.removeEventFilter(filt);page.close()

    def test_project_reload_and_page_populate_keep_volumes(self):
        project = Project('audio', 'video.mp4', audio_settings=AudioSettings(
            original_volume=70, dubbed_volume=80, additional_audio_volume=15))
        with tempfile.TemporaryDirectory() as folder:
            ProjectManager().save(project, folder)
            loaded = ProjectManager().load(folder)
            page = AudioPage();page.populate(loaded, project_dir=folder)
        self.assertEqual(page.orig_volume.value(), 70)
        self.assertEqual(page.dub_volume.value(), 80)
        self.assertEqual(page.add_volume.value(), 15)
        page.close()

    def test_ui_75_percent_and_mixer_contract(self):
        page = AudioPage();emitted=[];page.audio_settings_changed.connect(lambda *values: emitted.append(values))
        page.orig_volume.setValue(75)
        self.assertEqual(emitted[-1][0], 75)
        page.close()

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder);write_wav(root / 'audio' / 'source.wav')
            project = Project('audio', 'missing.mp4', metadata={'duration': 1.0},
                audio_settings=AudioSettings(original_volume=75, dubbed_volume=0))
            captured=[]
            def fake_run(command, *args, **kwargs):
                captured.append(command);write_wav(Path(command[-1]));return ''
            with patch('cartoon_sub.tts.final_mix_service.run_process', side_effect=fake_run):
                FinalAudioMixService().mix(project, root)
        graph = captured[0][captured[0].index('-filter_complex') + 1]
        self.assertIn('volume=0.7500', graph)


if __name__ == '__main__':
    unittest.main()
