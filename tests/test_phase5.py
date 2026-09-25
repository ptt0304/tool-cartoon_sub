import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import tempfile
import unittest
from pathlib import Path
from threading import Event
import pysubs2
from PySide6.QtCore import QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QPixmap, QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QHeaderView, QTableWidgetItem
from cartoon_sub.subtitle.models import DisplaySegment,Project,Segment,Mask,SubtitleStyle,LogoOverlay,WatermarkStyle
from cartoon_sub.subtitle.renderer import save_ass,validate_visuals
from cartoon_sub.media.preview import VideoRenderer
from cartoon_sub.media.process import run_process,CancelledError
from cartoon_sub.media.ffprobe import probe
from cartoon_sub.project.project_manager import ProjectManager
from cartoon_sub.ui.tabs.mask_style_tab import MaskStylePage, VIETNAMESE_FONTS
from cartoon_sub.ui.tabs.transcript_tab import build as build_transcript
from cartoon_sub.ui.tabs.subtitle_tab import SubtitlePage
from cartoon_sub.ui.timeline_table import create_table
from cartoon_sub.ui.docs_dialog import DocsWindow, TOPICS
from cartoon_sub.ui.main_window import MainWindow
from cartoon_sub.ui.settings_dialog import SettingsDialog
from cartoon_sub.ai.text_client import PROVIDERS, MODEL_PRESETS, PROVIDER_CATALOG


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
        p.mask = Mask(True, "gaussian", 20, 100, 280, 50)
        p.subtitle_style.center_in_mask = True
        with tempfile.TemporaryDirectory() as tmp:
            sub = pysubs2.load(str(save_ass(p, Path(tmp) / "centered.ass")))
        self.assertTrue(all(event.text.startswith(r"{\an5\pos(160,125)}") for event in sub))
        p.mask.enabled = False
        with self.assertRaises(ValueError):
            validate_visuals(p)

    def test_moving_watermark_and_overlay_settings_roundtrip(self):
        p = self.project()
        p.watermark = WatermarkStyle(text="Kênh thử nghiệm", font_size=24, transparency=35, speed=90)
        with tempfile.TemporaryDirectory() as tmp:
            logo = Path(tmp) / "logo.png"
            image = QPixmap(32, 24); image.fill(); image.save(str(logo))
            p.logos = [LogoOverlay("logo-1", str(logo), 10, 20, 32, 24, 15, 25, 24, 18, 50)]
            ProjectManager().save(p, tmp)
            loaded = ProjectManager().load(tmp)
            self.assertEqual(loaded.logos, p.logos)
            self.assertEqual(loaded.watermark, p.watermark)
            ass = pysubs2.load(str(save_ass(loaded, Path(tmp) / "watermark.ass")))
        watermark_events = [event for event in ass if event.style == "Watermark"]
        self.assertTrue(watermark_events)
        self.assertIn(r"\move", watermark_events[0].text)

    def test_controls_save_load_and_coordinate_mapping(self):
        p=self.project();page=MaskStylePage();page.load_project(p)
        self.assertEqual(page.values()[1].font,p.subtitle_style.font)
        self.assertGreaterEqual(page.font.count(),20)
        self.assertTrue(hasattr(page.canvas, 'draw_preview_subtitle'))
        page.canvas.resize(800,600);page.canvas.pixmap=QPixmap(1920,1080)
        self.assertEqual(page.canvas.point(QPointF(400,300)),(960,540))
        page.set_rectangle(10,120,280,40)
        page.strength.setValue(14)
        self.assertEqual(page.canvas.style.font,page.font.currentText())
        page.center_mask.setChecked(True)
        p.mask,p.subtitle_style=page.values()
        with tempfile.TemporaryDirectory() as tmp:
            ProjectManager().save(p,tmp);loaded=ProjectManager().load(tmp)
            self.assertEqual(loaded.mask,Mask(True,'solid',10,120,280,40,14))
            self.assertEqual(loaded.subtitle_style,p.subtitle_style)
            self.assertTrue(loaded.subtitle_style.center_in_mask)
        page.close()

    def test_dragging_logo_moves_it_and_updates_controls(self):
        page=MaskStylePage();page.canvas.resize(320,200);page.canvas.pixmap=QPixmap(320,180)
        page.canvas.logos=[LogoOverlay('drag','unused.png',10,15,40,30)]
        page.logo.addItem('Logo drag','drag');page.logo.setCurrentIndex(0)
        page.canvas.show();self.app.processEvents()
        rect=page.canvas.image_rect()
        start=QPoint(round(rect.x()+20),round(rect.y()+25))
        target=QPoint(round(rect.x()+100),round(rect.y()+75))
        QTest.mousePress(page.canvas,Qt.MouseButton.LeftButton,pos=start)
        QTest.mouseMove(page.canvas,target);QTest.mouseRelease(page.canvas,Qt.MouseButton.LeftButton,pos=target)
        logo=page.canvas.logos[0]
        self.assertEqual((logo.x,logo.y),(90,65))
        self.assertEqual((page.logo_controls[0].value(),page.logo_controls[1].value()),(90,65))
        page.close()

    def test_canvas_shows_selected_mask_effect(self):
        page=MaskStylePage();page.canvas.resize(320,200)
        source=QPixmap(320,180);source.fill(Qt.GlobalColor.red)
        page.canvas.pixmap=source;page.canvas.mask=Mask(True,'solid',40,40,120,60)
        page.canvas.show();self.app.processEvents()
        rect=page.canvas.image_rect();image=page.canvas.grab().toImage()
        center=image.pixelColor(round(rect.x()+100),round(rect.y()+70))
        self.assertLess(center.red(),80)
        page.canvas.mask=Mask(True,'gaussian',40,40,120,60)
        self.assertFalse(page.canvas.grab().isNull())
        page.canvas.mask=Mask(True,'solid',40,40,220,80)
        page.canvas.style=SubtitleStyle(font_size=18,outline=0,shadow=0)
        without_effects=page.canvas.grab().toImage()
        page.canvas.style=SubtitleStyle(font_size=18,outline=4,shadow=4)
        self.assertNotEqual(page.canvas.grab().toImage(),without_effects)
        page.close()

    def test_solid_white_uses_explicit_mask_and_text_colors(self):
        page=MaskStylePage();image=QImage(100,100,QImage.Format.Format_ARGB32);image.fill(Qt.GlobalColor.red)
        painter=QPainter(image);page.canvas.draw_mask_effect(painter,QRectF(10,10,50,50),10,10,50,50,'solid',10,'#FFFFFF');painter.end()
        color=image.pixelColor(30,30)
        self.assertGreater(color.red(),240);self.assertGreater(color.green(),240);self.assertGreater(color.blue(),240)
        p=self.project();p.mask=Mask(True,'solid',10,120,280,40,12,'#FFFFFF');p.subtitle_style.center_in_mask=True;p.subtitle_style.text_color='#123456'
        with tempfile.TemporaryDirectory() as tmp:
            subs=pysubs2.load(str(save_ass(p,Path(tmp)/'white.ass')))
        self.assertEqual(subs.styles['Default'].primarycolor, pysubs2.Color(0x12,0x34,0x56,0))
        page.close()

    def test_long_text_views_scroll_and_allow_resizing(self):
        mask_page=MaskStylePage();transcript=build_transcript();subtitle=SubtitlePage();timeline=create_table()
        for view in (transcript.table,subtitle.tree,timeline):
            self.assertEqual(view.horizontalScrollBarPolicy(),Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
            self.assertEqual(view.verticalScrollBarPolicy(),Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.assertEqual(transcript.table.horizontalHeader().sectionResizeMode(0),QHeaderView.ResizeMode.Interactive)
        self.assertEqual(mask_page.options_scroll.verticalScrollBarPolicy(),Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        mask_page.close();transcript.close();subtitle.close();timeline.close()

    def test_long_text_wraps_and_reflows_after_column_resize(self):
        transcript=build_transcript();transcript.table.setRowCount(1)
        transcript.table.setItem(0,6,QTableWidgetItem('Nội dung rất dài ' * 20));transcript.table.setColumnWidth(6,100)
        transcript.table.resizeRowsToContents()
        self.assertTrue(transcript.table.wordWrap())
        self.assertGreater(transcript.table.rowHeight(0),transcript.table.verticalHeader().minimumSectionSize())
        subtitle=SubtitlePage();self.assertTrue(subtitle.tree.wordWrap());self.assertFalse(subtitle.tree.uniformRowHeights())
        transcript.close();subtitle.close()

    def test_canvas_previews_watermark_and_selected_logo_scale(self):
        page=MaskStylePage();page.canvas.resize(320,200);page.canvas.pixmap=QPixmap(320,180);page.canvas.pixmap.fill(Qt.GlobalColor.red)
        without_watermark=page.canvas.grab().toImage();page.watermark_text.setText('Watermark thử');page.watermark_size.setValue(24);page.update_mask()
        self.assertNotEqual(page.canvas.grab().toImage(),without_watermark)
        first=LogoOverlay('one','unused.png',10,10,40,30,base_width=40,base_height=30)
        second=LogoOverlay('two','unused.png',10,10,20,20,base_width=20,base_height=20)
        page.canvas.logos=[first,second];page.logo.addItem('Logo one','one');page.logo.addItem('Logo two','two')
        page.logo.setCurrentIndex(0);page.logo_scale.setValue(200);page.logo_scale.editingFinished.emit()
        self.assertEqual((first.width,first.height,first.scale_percent),(80,60,200))
        self.assertEqual((second.width,second.height,second.scale_percent),(20,20,100))
        page.close()

    def test_docs_button_and_copyright_are_available(self):
        dialog=DocsWindow();self.assertEqual(len(TOPICS),10);self.assertIn('PHẠM THANH TÙNG',TOPICS[0][1]);self.assertIn('Master dialogue timeline',TOPICS[4][1]);self.assertIn('Δ target',TOPICS[4][1]);dialog.open_topic(3)
        self.assertEqual(len(dialog.topic_windows),1)
        window=MainWindow();self.assertEqual(window.docs_button.text(),'Docs');self.assertIn('PHẠM THANH TÙNG',window.copyright_label.text())
        for topic in dialog.topic_windows:topic.close()
        dialog.close();window.close()

    def test_application_icon_asset_is_available(self):
        from cartoon_sub.app.main import Path
        self.assertTrue((Path(__file__).resolve().parents[1] / "src" / "cartoon_sub" / "assets" / "cartoon_sub.ico").is_file())

    def test_text_provider_registry_has_ten_supported_choices(self):
        from cartoon_sub.app.settings import AISettings
        self.assertEqual(len(PROVIDER_CATALOG),10)
        for provider in ("gemini",*PROVIDERS):
            self.assertEqual(AISettings(translation_provider=provider).validate().translation_provider,provider)
            self.assertTrue(MODEL_PRESETS[provider])

    def test_settings_switches_translation_model_presets_by_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            key_file=Path(directory)/"api_key.txt"
            key_file.write_text("gemini_key: TEST\ndeepseek_key: TEST\nclaude_key: TEST\n",encoding="utf-8")
            window=MainWindow();dialog=SettingsDialog(window.controller);dialog.key_file.setText(str(key_file))
            dialog.provider.setCurrentIndex(dialog.provider.findData("deepseek"))
            self.assertEqual(dialog.translation_model.currentText(),"deepseek-v4-pro")
            dialog.provider.setCurrentIndex(dialog.provider.findData("anthropic"))
            self.assertIn(dialog.translation_model.currentText(),MODEL_PRESETS["anthropic"])
            dialog.close();window.close()

    def test_invalid_mask_rejected(self):
        p=self.project();p.mask=Mask(True,'gaussian',300,150,40,40)
        with self.assertRaises(ValueError):validate_visuals(p)
        p.mask=Mask(True,'gaussian',10,100,40,40,21)
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
            for kind in ('solid','gaussian'):
                p.mask=Mask(True,kind,11,131,280,36,14)
                output=renderer.render(p,root,2,True)
                info=probe(output)
                self.assertAlmostEqual(info['duration'],10,delta=.25)
                self.assertEqual((info['width'],info['height']),(320,180))
                self.assertIsNotNone(info['audio_codec'])
            logo = root / 'logo.png'
            image = QPixmap(24, 24); image.fill(); image.save(str(logo))
            p.logos = [LogoOverlay('logo', str(logo), 12, 15, 24, 24, 10, 20)]
            output = renderer.render(p,root,2,True)
            self.assertTrue(output.is_file())
            p.logos = []
            p.mask.enabled=False
            output=renderer.render(p,root,preview=False)
            self.assertAlmostEqual(probe(output)['duration'],12,delta=.25)
            cancelled=Event();cancelled.set()
            with self.assertRaises(CancelledError):renderer.render(p,root,cancel=cancelled)
            self.assertFalse(list(root.rglob('rendering.mp4')))
