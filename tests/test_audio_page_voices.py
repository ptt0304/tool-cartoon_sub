import os
import unittest
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from cartoon_sub.subtitle.models import Project, Segment
from cartoon_sub.ui.tabs.audio_tab import AudioPage
_app=QApplication.instance() or QApplication([])

def voices(): return [{"voice_id":"voice_a","display_name":"A","status":"READY","favorite":True},{"voice_id":"voice_b","display_name":"B","status":"READY","favorite":False}]
class AudioPageVoiceTests(unittest.TestCase):
    def test_project_voice_is_selected_and_persisted_on_project_settings(self):
        project=Project("voice","source.mp4",segments=[Segment(1,0,1,"你好")]); project.audio_settings.tts_voice_id="voice_b"
        page=AudioPage(); page.populate(project,voices())
        self.assertEqual(page.tts_voice.currentData(),"voice_b")
    def test_voice_selector_has_no_speaker_rows_or_mapping_signal(self):
        project=Project("voice","source.mp4",segments=[Segment(1,0,1,"你好")]); page=AudioPage(); page.populate(project,voices())
        self.assertFalse(hasattr(page,"tts_table")); self.assertFalse(hasattr(page,"batch_voice"))
    def test_missing_project_voice_remains_explicit(self):
        project=Project("voice","source.mp4"); project.audio_settings.tts_voice_id="removed"
        page=AudioPage(); page.populate(project,voices())
        self.assertEqual(page.tts_voice.currentData(),"removed"); self.assertEqual(page.tts_voice.currentText(),"Missing voice: removed")
if __name__=="__main__": unittest.main()
