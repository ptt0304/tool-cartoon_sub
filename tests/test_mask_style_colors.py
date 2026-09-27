import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pysubs2
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QImage, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

from cartoon_sub.project.project_manager import ProjectManager
from cartoon_sub.subtitle.models import Mask, Project, SubtitleStyle, Utterance
from cartoon_sub.subtitle.renderer import save_ass
from cartoon_sub.subtitle.renderer import validate_visuals
from cartoon_sub.media.preview import VideoRenderer
from cartoon_sub.ui.tabs.mask_style_tab import MaskStylePage


class MaskStyleColorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def project():
        return Project('mask-colors', 'missing.mp4', metadata={'width': 320, 'height': 180, 'duration': 5},
            segments=[Utterance(1, 0, 2, '你好', vi='Xin chào')])

    def test_text_preview_renders_and_updates_fill_outline(self):
        page = MaskStylePage();page.resize(700, 400);page.canvas.resize(400, 250)
        page.canvas.pixmap = QPixmap(320, 180);page.canvas.pixmap.fill(Qt.GlobalColor.gray)
        page.outline.setValue(3);page.canvas.show();self.app.processEvents()
        initial = page.canvas.grab().toImage()
        page.text_color.set_color('#FF0000');self.app.processEvents()
        text_changed = page.canvas.grab().toImage()
        page.outline_color.set_color('#00FF00');self.app.processEvents()
        outline_changed = page.canvas.grab().toImage()
        self.assertFalse(initial.isNull())
        self.assertNotEqual(initial, text_changed)
        self.assertNotEqual(text_changed, outline_changed)
        self.assertEqual(page.canvas.style.text_color, '#FF0000')
        self.assertEqual(page.canvas.style.outline_color, '#00FF00')
        page.close()

    def test_solid_uses_mask_color_including_white(self):
        page = MaskStylePage()
        for chosen in ('#2468AC', '#FFFFFF'):
            image = QImage(80, 80, QImage.Format.Format_ARGB32);image.fill(Qt.GlobalColor.red)
            painter = QPainter(image)
            page.canvas.draw_mask_effect(painter, QRectF(10, 10, 50, 50), 10, 10, 50, 50, 'solid', 12, chosen)
            painter.end()
            self.assertEqual(image.pixelColor(30, 30).name().upper(), chosen)
        page.close()

    def test_gaussian_ignores_mask_color(self):
        page = MaskStylePage();source = QPixmap(80, 80);source.fill(Qt.GlobalColor.red);page.canvas.pixmap = source
        rendered = []
        for chosen in ('#000000', '#FFFFFF'):
            image = QImage(80, 80, QImage.Format.Format_ARGB32);image.fill(Qt.GlobalColor.blue)
            painter = QPainter(image)
            page.canvas.draw_mask_effect(painter, QRectF(10, 10, 50, 50), 10, 10, 50, 50, 'gaussian', 12, chosen)
            painter.end();rendered.append(image)
        self.assertEqual(rendered[0], rendered[1])
        page.close()

    def test_no_mask_option_preview_and_controls(self):
        page = MaskStylePage();page.resize(700, 400);page.canvas.resize(400, 250)
        self.assertEqual([page.kind.itemText(i) for i in range(page.kind.count())],
                         ['Solid', 'Gaussian', 'No mask'])
        page.canvas.pixmap = QPixmap(320, 180);page.canvas.pixmap.fill(Qt.GlobalColor.gray)
        page.enabled.setChecked(True);page.kind.setCurrentIndex(page.kind.findData('none'))
        page.canvas.show();self.app.processEvents()
        no_mask = page.canvas.grab().toImage()
        page.enabled.setChecked(False);self.app.processEvents()
        mask_off = page.canvas.grab().toImage()
        self.assertEqual(no_mask, mask_off)
        self.assertFalse(page.mask_color.isEnabled())
        self.assertFalse(page.strength.isEnabled())
        page.kind.setCurrentIndex(page.kind.findData('gaussian'))
        self.assertTrue(page.mask_color.isEnabled())
        self.assertTrue(page.strength.isEnabled())
        page.close()

    def test_no_mask_roundtrip_and_final_filter_is_text_only(self):
        project = self.project()
        project.mask = Mask(True, 'none', 10, 120, 300, 50, 12, '#2468AC')
        project.subtitle_style = SubtitleStyle(font_size=24, outline=3, shadow=2, text_color='#123456', center_in_mask=True)
        validate_visuals(project)
        with tempfile.TemporaryDirectory() as folder:
            ProjectManager().save(project, folder)
            loaded = ProjectManager().load(folder)
            self.assertEqual(loaded.mask.kind, 'none')
            subs = pysubs2.load(str(save_ass(loaded, Path(folder) / 'no-mask.ass')))
            self.assertTrue(all(event.text.startswith(r'{\an5\pos(160,145)}') for event in subs))
            source = Path(folder) / 'source.mp4';source.write_bytes(b'source')
            loaded.source_video_path = str(source)
            captured = []
            def fake_run(args, *_args, **_kwargs):
                captured.append(args)
                Path(args[-1]).write_bytes(b'rendered')
            with patch('cartoon_sub.media.preview.run_process', side_effect=fake_run):
                output = VideoRenderer().render(loaded, folder, preview=True)
            self.assertTrue(output.is_file())
            filters = captured[0][captured[0].index('-filter_complex') + 1]
            self.assertIn('[0:v]null[masked];', filters)
            self.assertIn('ass=filename=subtitle.ass', filters)
            self.assertNotIn('drawbox=', filters)
            self.assertNotIn('gblur=', filters)

    def test_ass_and_project_roundtrip_keep_three_colors(self):
        project = self.project()
        project.mask = Mask(True, 'solid', 0, 120, 320, 60, 12, '#F0E0D0')
        project.subtitle_style = SubtitleStyle(text_color='#123456', outline_color='#ABCDEF')
        with tempfile.TemporaryDirectory() as folder:
            ProjectManager().save(project, folder)
            loaded = ProjectManager().load(folder)
            subs = pysubs2.load(str(save_ass(loaded, Path(folder) / 'colors.ass')))
        self.assertEqual(loaded.mask.mask_color, '#F0E0D0')
        self.assertEqual(loaded.subtitle_style.text_color, '#123456')
        self.assertEqual(loaded.subtitle_style.outline_color, '#ABCDEF')
        self.assertEqual(subs.styles['Default'].primarycolor, pysubs2.Color(0x12, 0x34, 0x56, 0))
        self.assertEqual(subs.styles['Default'].outlinecolor, pysubs2.Color(0xAB, 0xCD, 0xEF, 0))

    def test_old_project_defaults_and_white_mode_migration(self):
        base = {'name': 'old', 'source_video_path': 'missing.mp4', 'schema_version': 3}
        old = Project.from_dict(base)
        self.assertEqual(old.mask.mask_color, '#000000')
        self.assertEqual(old.subtitle_style.text_color, '#FFFFFF')
        self.assertEqual(old.subtitle_style.outline_color, '#000000')
        migrated = Project.from_dict({**base, 'mask': {'kind': 'white_background'}, 'subtitle_style': {}})
        self.assertEqual((migrated.mask.kind, migrated.mask.mask_color), ('solid', '#FFFFFF'))
        self.assertEqual(migrated.subtitle_style.text_color, '#000000')


if __name__ == '__main__':
    unittest.main()
