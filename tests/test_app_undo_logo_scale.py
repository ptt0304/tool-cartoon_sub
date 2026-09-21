import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtGui import QUndoStack
from PySide6.QtWidgets import QApplication

from cartoon_sub.project.project_manager import ProjectManager
from cartoon_sub.subtitle.models import LogoOverlay, Mask, Project, Utterance
from cartoon_sub.ui.main_window import MainWindow
from cartoon_sub.ui.tabs.mask_style_tab import MaskStylePage
from cartoon_sub.ui.undo import ValueCommand
from cartoon_sub.ui.docs_dialog import TOPICS


class AppUndoLogoScaleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_shared_stack_numeric_undo_and_redo(self):
        state = {'value': 20};stack = QUndoStack()
        stack.push(ValueCommand('Numeric edit', 20, 50, lambda value: state.update(value=value)))
        self.assertEqual(state['value'], 50)
        stack.undo();self.assertEqual(state['value'], 20)
        stack.redo();self.assertEqual(state['value'], 50)

    def test_logo_scale_signed_original_based_undo_and_persistence(self):
        project = Project('logo', 'video.mp4', logos=[LogoOverlay(
            'one', 'unused.png', width=40, height=30, base_width=40, base_height=30,
            scale_percent=100)])
        page = MaskStylePage();stack = QUndoStack();page.set_undo_stack(stack);page.load_project(project)
        page.logo.setCurrentIndex(0)
        page.logo_scale.setValue(250);page.logo_scale.editingFinished.emit()
        page.logo_scale.setValue(1000);page.logo_scale.editingFinished.emit()
        self.assertEqual((project.logos[0].width, project.logos[0].height), (400, 300))
        stack.undo()
        self.assertEqual(project.logos[0].scale_percent, 250)
        self.assertEqual((project.logos[0].width, project.logos[0].height), (100, 75))
        with tempfile.TemporaryDirectory() as folder:
            ProjectManager().save(project, folder);loaded = ProjectManager().load(folder)
        self.assertEqual(loaded.logos[0].scale_percent, 250)

        page.logo_scale.setValue(-504);page.logo_scale.editingFinished.emit()
        self.assertEqual(project.logos[0].scale_percent, -504)
        self.assertEqual((project.logos[0].width, project.logos[0].height), (202, 151))
        self.assertAlmostEqual(project.logos[0].width / project.logos[0].height, 40 / 30, places=2)
        page.close()

    def test_legacy_logo_scale_migrates_to_new_percent(self):
        project = Project.from_dict({'name':'old','source_video_path':'video.mp4','schema_version':3,
            'logos':[{'id':'one','path':'logo.png','width':80,'height':60,
                      'base_width':40,'base_height':30,'scale':100}]})
        self.assertEqual(project.logos[0].scale_percent, 200)

    def test_mask_rectangle_undo_updates_project(self):
        project = Project('mask', 'video.mp4', mask=Mask(True, 'solid', 20, 30, 100, 40))
        page = MaskStylePage();stack = QUndoStack();page.set_undo_stack(stack);page.load_project(project)
        page.coords[0].setValue(50);page.coords[0].editingFinished.emit()
        self.assertEqual(project.mask.x, 50)
        stack.undo()
        self.assertEqual(project.mask.x, 20)
        self.assertEqual(page.coords[0].value(), 20)
        page.close()

    def test_logo_drag_is_one_undo_operation(self):
        logo=LogoOverlay('one','unused.png',40,30,40,30,base_width=40,base_height=30,scale_percent=100)
        project=Project('logo','video.mp4',logos=[logo]);page=MaskStylePage();stack=QUndoStack();page.set_undo_stack(stack);page.load_project(project)
        page.logo.setCurrentIndex(0);logo.x,logo.y=140,90;page.logo_moved('one',140,90);page.commit_logo_drag('one')
        self.assertEqual(stack.count(),1);stack.undo();self.assertEqual((project.logos[0].x,project.logos[0].y),(40,30))
        page.close()

    def test_translate_text_batch_voice_and_project_switch_reset(self):
        with tempfile.TemporaryDirectory() as first_dir, tempfile.TemporaryDirectory() as second_dir:
            root = Path(first_dir);source = root / 'video.mp4';source.write_bytes(b'x')
            speakers = {f'SPK_0{i}': {'id':f'SPK_0{i}','name':str(i),'tts_voice_id':f'old{i}','tts_speed':1.0}
                        for i in range(1,4)}
            project = Project('first', str(source), segments=[Utterance(1,0,2,'中',vi='A',speaker_id='SPK_01')], speakers=speakers)
            window = MainWindow();window.controller.accept((project,root));window.refresh();window.undo_stack.clear()

            before = window._project_state()
            window.controller.edit_utterance(1,'B','B','balanced_dubbing',0)
            window._record_project_edit(before,'Edit text')
            self.assertEqual(window.controller.project.utterances[0].vi_subtitle,'B')
            window.undo_stack.undo();self.assertEqual(window.controller.project.utterances[0].vi_subtitle,'A')

            window.apply_batch_voice(list(speakers),'new_voice')
            self.assertEqual({s['tts_voice_id'] for s in window.controller.project.speakers.values()},{'new_voice'})
            window.undo_stack.undo()
            self.assertEqual([window.controller.project.speakers[f'SPK_0{i}']['tts_voice_id'] for i in range(1,4)],
                             ['old1','old2','old3'])
            self.assertTrue(window.undo_stack.canRedo())

            root2=Path(second_dir);source2=root2/'video.mp4';source2.write_bytes(b'x')
            window.accept_project((Project('second',str(source2)),root2))
            self.assertFalse(window.undo_stack.canUndo());self.assertFalse(window.undo_stack.canRedo())
            window.close()

    def test_docs_settings_and_undo_logo_help(self):
        self.assertEqual(TOPICS[2][0], '3. Settings')
        self.assertIn('Ctrl+Z', TOPICS[0][1])
        self.assertIn('1000%', TOPICS[6][1])
        self.assertIn('-504%', TOPICS[6][1])


if __name__ == '__main__':
    unittest.main()
