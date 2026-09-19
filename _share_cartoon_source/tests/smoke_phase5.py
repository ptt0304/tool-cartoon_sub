"""Explicit local video visual QA; source project is read-only."""
import os
os.environ['QT_QPA_PLATFORM']='offscreen'
from pathlib import Path
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFontDatabase,QFont
from cartoon_sub.project.project_manager import ProjectManager
from cartoon_sub.media.preview import VideoRenderer
from cartoon_sub.media.process import run_process
from cartoon_sub.ui.tabs.mask_style_tab import MaskStylePage
from cartoon_sub.subtitle.models import Mask

app=QApplication([])
for font in ('C:/Windows/Fonts/segoeui.ttf','C:/Windows/Fonts/msyh.ttc'):QFontDatabase.addApplicationFont(font)
app.setFont(QFont('Segoe UI',10))
source=Path('4/project.json');before=source.read_bytes()
p=ProjectManager().load(source)
root=Path('docs/phase5_check').resolve();root.mkdir(exist_ok=True)
renderer=VideoRenderer();frame=renderer.frame(p,root,55)
w,h=p.metadata['width'],p.metadata['height']
p.mask=Mask(True,'solid',round(w*.1),round(h*.82),round(w*.8),round(h*.12))
p.subtitle_style.margin_bottom=round(h*.07)
page=MaskStylePage();page.resize(1280,800);page.load_project(p);page.show_frame(frame);page.show();app.processEvents()
page.grab().save(str(root/'mask_style_ui.png'))
output=renderer.render(p,root,55,True)
run_process(['ffmpeg','-nostdin','-n','-ss','1','-i',str(output),'-frames:v','1','-update','1',str(root/'rendered_frame.png')])
page.show_preview(output);app.processEvents();page.close()
assert source.read_bytes()==before
print('PASS actual project preview, UI and source unchanged:',output)
