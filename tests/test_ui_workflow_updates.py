import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDoubleSpinBox, QScrollArea, QSpinBox, QVBoxLayout, QWidget

from cartoon_sub.app.controller import Controller
from cartoon_sub.app.settings import AISettings
from cartoon_sub.project.project_manager import ProjectManager
from cartoon_sub.speaker.service import approve_review
from cartoon_sub.subtitle.models import Project, Segment
from cartoon_sub.translation.context_service import source_fingerprint
from cartoon_sub.translation.pipeline import TranslationPipeline
from cartoon_sub.ui.main_window import MainWindow
from cartoon_sub.ui.no_wheel import NoWheelNumericFilter
from cartoon_sub.ui.tabs.audio_tab import AudioPage


class UIWorkflowUpdateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_no_wheel_numeric_inputs_and_parent_scroll(self):
        scroll = QScrollArea(); body = QWidget(); layout = QVBoxLayout(body)
        spins = [QSpinBox(), QDoubleSpinBox()]
        for spin in spins: spin.setValue(10); layout.addWidget(spin)
        body.setMinimumHeight(1000); scroll.setWidget(body); scroll.setWidgetResizable(True); scroll.resize(200, 100); scroll.show()
        filt = NoWheelNumericFilter(scroll); self.app.installEventFilter(filt); self.app.processEvents()
        before = scroll.verticalScrollBar().value()
        for spin in spins:
            event = QWheelEvent(QPointF(5,5), QPointF(5,5), QPoint(), QPoint(0,-120),
                                Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                                Qt.ScrollPhase.ScrollUpdate, False)
            QApplication.sendEvent(spin, event)
            self.assertEqual(spin.value(), 10)
        self.assertGreaterEqual(scroll.verticalScrollBar().value(), before)
        self.app.removeEventFilter(filt); scroll.close()

    def test_vietnamese_srt_import_and_ai_priority_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / "source.mp4"; source.write_bytes(b"x")
            project = Project("p", str(source), segments=[Segment(1,0,2,"一"), Segment(2,2,4,"二")])
            ProjectManager().save(project, root)
            controller = Controller(); controller.accept((project, root))
            srt = root / "vi.srt"; srt.write_text("1\n00:00:00,000 --> 00:00:02,000\nMột\n\n2\n00:00:02,000 --> 00:00:04,000\nHai\n", encoding="utf-8")
            result = controller.import_vietnamese_subtitles(srt)
            self.assertEqual(result, {"imported": 2, "unmatched": 0, "conflicts": 0})
            self.assertEqual([s.vi_subtitle for s in project.utterances], ["Một", "Hai"])
            self.assertTrue(all(s.translation_source == "imported_srt" for s in project.utterances))

            project.speakers = {"SPK_01": {"id":"SPK_01", "name":"One"}}
            for segment in project.utterances: segment.speaker_id = "SPK_01"
            approve_review(project); project.context_source_hash = source_fingerprint(project)
            ProjectManager().save(project, root)
            store = Mock(); store.load.return_value = AISettings()
            factory = Mock(side_effect=AssertionError("AI must not be called for imported rows"))
            translated, _ = TranslationPipeline(store, factory).run(project, root)
            self.assertEqual([s.vi_subtitle for s in translated.utterances], ["Một", "Hai"])
            factory.assert_not_called()

    def test_main_window_fits_screen_and_wraps_all_tabs(self):
        window = MainWindow(); geometry = window.screen().availableGeometry()
        self.assertLessEqual(window.width(), geometry.width())
        self.assertLessEqual(window.height(), geometry.height())
        self.assertEqual(len(window.tab_scrolls), 7)
        self.assertTrue(all(isinstance(scroll, QScrollArea) and scroll.widgetResizable()
                            for scroll in window.tab_scrolls))
        window.close()

    def test_tab_progress_is_determinate_and_cancel_feedback_is_immediate(self):
        window = MainWindow(); window.active_job_tab = 2
        panel = window.tab_job_panels[2]; panel.show()
        window.update_job_progress("Dịch nhóm 4/10")
        self.assertEqual((panel.progress_bar.value(), panel.progress_bar.maximum()), (4,10))
        fake = Mock(); window.worker = fake; window.cancel_job()
        fake.cancel.assert_called_once_with()
        self.assertEqual(panel.status_label.text(), "Cancelling...")
        self.assertFalse(panel.cancel_button.isEnabled())
        window.worker = None; window.close()

    def test_transcript_updates_only_its_single_progress_row(self):
        window = MainWindow(); window.active_job_tab = 1
        panel = window.tab_job_panels[1]; panel.show()
        window.progress.hide(); window.cancel_button.hide()
        window.update_job_progress("Transcribing chunk 3/10")
        self.assertEqual((panel.progress_bar.value(), panel.progress_bar.maximum()), (3,10))
        self.assertNotEqual(window.statusBar().currentMessage(), "Transcribing chunk 3/10")
        self.assertFalse(panel.isHidden())
        self.assertFalse(window.progress.isVisible())
        self.assertFalse(window.cancel_button.isVisible())
        fake = Mock(); window.worker = fake; window.cancel_job()
        fake.cancel.assert_called_once_with()
        self.assertFalse(panel.cancel_button.isEnabled())
        window.worker = None; window.close()

    def test_audio_batch_voice_checks_only_selected_speakers(self):
        page = AudioPage(); emitted=[]; page.batch_voice_requested.connect(lambda ids, voice: emitted.append((ids,voice)))
        project = Project("p", "v", speakers={f"SPK_0{i}": {"id":f"SPK_0{i}","name":str(i)} for i in range(1,5)})
        voices=[{"voice_id":"voice_x","display_name":"Voice X","status":"READY"}]
        page.populate(project, voices)
        for row in (0,2,3): page.tts_table.item(row,0).setCheckState(Qt.CheckState.Checked)
        page.batch_voice.setCurrentIndex(page.batch_voice.findData("voice_x")); page._apply_batch_voice()
        self.assertEqual(emitted, [(["SPK_01","SPK_03","SPK_04"], "voice_x")])
        page.close()

    def test_voice_sync_uses_revision_preserves_selection_and_keeps_cache_offline(self):
        window = MainWindow()
        self.assertEqual(window.voice_sync_timer.interval(), 3000)
        old = [{"voice_id": "voice_b", "display_name": "B", "status": "READY", "favorite": False}]
        window.local_tts_voice_revision = "rev-1"
        window.local_tts_voices = old
        page = window.pages[5]
        page.populate = Mock()
        window.accept_voice_library_sync({
            "revision": "rev-1", "voices": [{"voice_id": "ignored"}], "all_voice_ids": ["ignored"],
        })
        page.populate.assert_not_called()
        self.assertIs(window.local_tts_voices, old)

        window.controller.project = Project("p", "v", speakers={
            "SPK_01": {"id": "SPK_01", "name": "One", "tts_voice_id": "voice_b", "tts_speed": 1.0},
        })
        updated = [
            {"voice_id": "voice_a", "display_name": "A", "status": "READY", "favorite": True},
            {"voice_id": "voice_b", "display_name": "B", "status": "READY", "favorite": False},
        ]
        window.accept_voice_library_sync({"revision": "rev-2", "voices": updated,
                                          "all_voice_ids": ["voice_a", "voice_b"]})
        page.populate.assert_called_once()
        self.assertEqual(window.controller.project.speakers["SPK_01"]["tts_voice_id"], "voice_b")
        window.voice_library_sync_failed("offline")
        self.assertIs(window.local_tts_voices, updated)
        window.controller.project = None
        window.close()


if __name__ == "__main__": unittest.main()
