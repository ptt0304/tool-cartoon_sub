import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import tempfile
import unittest
from pathlib import Path
from threading import Event
import pysubs2
from PySide6.QtCore import QPointF
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication
from cartoon_sub.subtitle.models import DisplaySegment,Project,Segment,Mask,SubtitleStyle
from cartoon_sub.subtitle.renderer import save_ass,validate_visuals
from cartoon_sub.media.preview import VideoRenderer
from cartoon_sub.media.process import run_process,CancelledError
from cartoon_sub.media.ffprobe import probe
from cartoon_sub.project.project_manager import ProjectManager
from cartoon_sub.ui.tabs.mask_style_tab import MaskStylePage, TEST_SUBTITLE, VIETNAMESE_FONTS


class Phase5Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def project(self):
        return Project('render','missing.mp4',metadata={'width':320,'height':180,'duration':12},
            segments=[Segment(1,1,4,'中文',vi='Chào bạn',speaker_id='SPK_01'),
                      Segment(2,3,6,'你好',vi='Xin chào',speaker_id='SPK_02')],
            subtitle_style=SubtitleStyle(font_size=18,margin_bottom=12))

    def test_ass_clips_preview_preserves_overlap_and_source(self):
        p=self.project();before=p.to_dict()
        with tempfile.TemporaryDirectory() as tmp:
            path=save_ass(p,Path(tmp)/'test.ass',2,3)
            sub=pysubs2.load(str(path))
            self.assertEqual([(s.start,s.end) for s in sub],[(0,2000),(1000,3000)])
            self.assertEqual(p.to_dict(),before)
            self.assertEqual(sub.info['PlayResX'],'320')

    def test_ass_uses_display_segments_when_they_exist(self):
        p = self.project()
        p.segments[0].set_display_segments([
            DisplaySegment("1.1", 1, 1, 2.5, "Chào", segmentation_reason="local"),
            DisplaySegment("1.2", 1, 2.5, 4, "bạn", segmentation_reason="local"),
        ])
        with tempfile.TemporaryDirectory() as tmp:
            sub = pysubs2.load(str(save_ass(p, Path(tmp) / "display.ass")))
        self.assertEqual([(event.start, event.end, event.plaintext) for event in sub],
            [(1000, 2500, "Chào"), (2500, 4000, "bạn"), (3000, 6000, "Xin chào")])

    def test_ass_centers_subtitles_in_enabled_mask(self):
        p = self.project()
        p.mask = Mask(True, "blur", 20, 100, 280, 50)
        p.subtitle_style.center_in_mask = True
        with tempfile.TemporaryDirectory() as tmp:
            sub = pysubs2.load(str(save_ass(p, Path(tmp) / "centered.ass")))
        self.assertTrue(all(event.text.startswith(r"{\an5\pos(160,125)}") for event in sub))
        p.mask.enabled = False
        with self.assertRaises(ValueError):
            validate_visuals(p)

    def test_controls_save_load_and_coordinate_mapping(self):
        p=self.project();page=MaskStylePage();page.load_project(p)
        self.assertEqual(page.values()[1].font,p.subtitle_style.font)
        self.assertGreaterEqual(page.font.count(),20)
        self.assertIn('PHẠM THANH TÙNG',TEST_SUBTITLE)
        page.canvas.resize(800,600);page.canvas.pixmap=QPixmap(1920,1080)
        self.assertEqual(page.canvas.point(QPointF(400,300)),(960,540))
        page.set_rectangle(10,120,280,40)
        self.assertEqual(page.canvas.style.font,page.font.currentText())
        page.center_mask.setChecked(True)
        p.mask,p.subtitle_style=page.values()
        with tempfile.TemporaryDirectory() as tmp:
            ProjectManager().save(p,tmp);loaded=ProjectManager().load(tmp)
            self.assertEqual(loaded.mask,Mask(True,'solid',10,120,280,40))
            self.assertEqual(loaded.subtitle_style,p.subtitle_style)
            self.assertTrue(loaded.subtitle_style.center_in_mask)
        page.close()

    def test_invalid_mask_rejected(self):
        p=self.project();p.mask=Mask(True,'blur',300,150,40,40)
        with self.assertRaises(ValueError):validate_visuals(p)

    def test_style_does_not_invalidate_translation(self):
        from cartoon_sub.translation.pipeline import translation_fingerprint
        from cartoon_sub.app.settings import AISettings
        p=self.project();before=translation_fingerprint(p,AISettings())
        p.mask=Mask(True,'solid',0,140,320,30);p.subtitle_style.font_size=25
        self.assertEqual(translation_fingerprint(p,AISettings()),before)

    def test_real_ffmpeg_solid_blur_final_and_cancel(self):
        with tempfile.TemporaryDirectory(prefix='mask space ') as tmp:
            root=Path(tmp);p=self.project();source=root/'source.mp4';p.source_video_path=str(source)
            run_process(['ffmpeg','-nostdin','-n','-f','lavfi','-i','testsrc2=size=320x180:rate=12',
                '-f','lavfi','-i','sine=frequency=400:sample_rate=44100','-t','12',
                '-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac',str(source)])
            renderer=VideoRenderer()
            self.assertTrue(renderer.frame(p,root,2).is_file())
            for kind in ('solid','blur'):
                p.mask=Mask(True,kind,11,131,280,36)
                output=renderer.render(p,root,2,True)
                info=probe(output)
                self.assertAlmostEqual(info['duration'],10,delta=.25)
                self.assertEqual((info['width'],info['height']),(320,180))
                self.assertIsNotNone(info['audio_codec'])
            p.mask.enabled=False
            output=renderer.render(p,root,preview=False)
            self.assertAlmostEqual(probe(output)['duration'],12,delta=.25)
            cancelled=Event();cancelled.set()
            with self.assertRaises(CancelledError):renderer.render(p,root,cancel=cancelled)
            self.assertFalse(list(root.rglob('rendering.mp4')))
