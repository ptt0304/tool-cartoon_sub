from PySide6.QtCore import Qt, QRectF, Signal, QUrl
from PySide6.QtGui import QPixmap, QPainter, QColor, QPen, QFont
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QFormLayout,QLabel,QPushButton,
    QSpinBox,QDoubleSpinBox,QCheckBox,QComboBox,QFontComboBox,QTabWidget,QScrollArea)
from PySide6.QtMultimedia import QMediaPlayer,QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget
from cartoon_sub.subtitle.models import Mask,SubtitleStyle


class MaskCanvas(QWidget):
    selected = Signal(int,int,int,int)
    def __init__(self):
        super().__init__();self.setMinimumSize(320,200)
        self.pixmap=QPixmap();self.mask=Mask();self.origin=None;self.drag=None
    def image_rect(self):
        if self.pixmap.isNull():return QRectF()
        size=self.pixmap.size().scaled(self.size(),Qt.AspectRatioMode.KeepAspectRatio)
        return QRectF((self.width()-size.width())/2,(self.height()-size.height())/2,size.width(),size.height())
    def point(self,p):
        r=self.image_rect()
        return (max(0,min(self.pixmap.width(),round((p.x()-r.x())*self.pixmap.width()/r.width()))),
                max(0,min(self.pixmap.height(),round((p.y()-r.y())*self.pixmap.height()/r.height()))))
    def mousePressEvent(self,e):
        if e.button()==Qt.MouseButton.LeftButton and self.image_rect().contains(e.position()):
            self.origin=self.point(e.position());self.drag=None
    def mouseMoveEvent(self,e):
        if self.origin:
            p=self.point(e.position());x,y=self.origin
            self.drag=(min(x,p[0]),min(y,p[1]),abs(x-p[0]),abs(y-p[1]));self.update()
    def mouseReleaseEvent(self,e):
        if e.button()==Qt.MouseButton.LeftButton and self.origin:
            self.mouseMoveEvent(e)
            if self.drag and min(self.drag[2:])>=2:self.selected.emit(*self.drag)
            self.origin=None;self.drag=None;self.update()
    def paintEvent(self,e):
        p=QPainter(self);p.fillRect(self.rect(),QColor('#20252b'));r=self.image_rect()
        if r.isEmpty():
            p.setPen(Qt.GlobalColor.white);p.drawText(self.rect(),Qt.AlignmentFlag.AlignCenter,'Lấy khung hình rồi kéo vùng che phụ đề Trung.');return
        p.drawPixmap(r.toRect(),self.pixmap);m=self.mask
        box=self.drag or ((m.x,m.y,m.width,m.height) if m.enabled else None)
        if box:
            x,y,w,h=box;sx=r.width()/self.pixmap.width();sy=r.height()/self.pixmap.height()
            rect=QRectF(r.x()+x*sx,r.y()+y*sy,w*sx,h*sy)
            p.fillRect(rect,QColor(0,160,255,55));p.setPen(QPen(QColor('#00baff'),2));p.drawRect(rect)


class MaskStylePage(QWidget):
    def __init__(self):
        super().__init__();self.loading=False
        outer=QVBoxLayout(self)
        note=QLabel('Lấy khung hình → Kéo vùng che → Chỉnh chữ → Tạo preview → Render MP4. Dùng bản VI Subtitle; xử lý local.');note.setWordWrap(True);outer.addWidget(note)
        bar=QHBoxLayout();self.time=QDoubleSpinBox();self.time.setDecimals(2);self.time.setSuffix(' s');bar.addWidget(self.time)
        for name,label in [('frame_button','Lấy khung hình'),('preview_button','Tạo preview 10 giây'),('render_button','Render toàn bộ MP4'),('save_button','Lưu Mask / Style')]:
            b=QPushButton(label);setattr(self,name,b);bar.addWidget(b)
        outer.addLayout(bar);body=QHBoxLayout();outer.addLayout(body,1)
        self.views=QTabWidget();self.canvas=MaskCanvas();self.views.addTab(self.canvas,'Chọn vùng che')
        playback=QWidget();pv=QVBoxLayout(playback);self.video=QVideoWidget();pv.addWidget(self.video,1)
        buttons=QHBoxLayout();self.play_button=QPushButton('Phát preview');self.stop_button=QPushButton('Dừng');buttons.addWidget(self.play_button);buttons.addWidget(self.stop_button);pv.addLayout(buttons)
        self.views.addTab(playback,'Video preview');body.addWidget(self.views,1)
        self.player=QMediaPlayer(self);self.audio=QAudioOutput(self);self.player.setAudioOutput(self.audio);self.player.setVideoOutput(self.video)
        self.play_button.clicked.connect(self.player.play);self.stop_button.clicked.connect(self.player.stop)
        panel=QWidget();form=QFormLayout(panel);scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setWidget(panel);scroll.setMaximumWidth(300);body.addWidget(scroll)
        self.enabled=QCheckBox('Bật vùng che');form.addRow(self.enabled)
        self.kind=QComboBox();self.kind.addItems(['solid','blur']);form.addRow('Kiểu mask',self.kind)
        self.coords=[]
        for label in ('X','Y','Width','Height'):
            spin=QSpinBox();spin.setRange(0,32768);form.addRow(label,spin);self.coords.append(spin);spin.valueChanged.connect(self.update_mask)
        self.font=QFontComboBox();form.addRow('Font',self.font)
        self.size=QSpinBox();self.size.setRange(8,300);form.addRow('Cỡ chữ (pixel)',self.size)
        self.bold=QCheckBox();form.addRow('Đậm',self.bold)
        self.outline=QDoubleSpinBox();self.outline.setRange(0,20);form.addRow('Viền',self.outline)
        self.shadow=QDoubleSpinBox();self.shadow.setRange(0,20);form.addRow('Bóng',self.shadow)
        self.alignment=QComboBox()
        for i,name in enumerate(['Dưới trái','Dưới giữa','Dưới phải','Giữa trái','Chính giữa','Giữa phải','Trên trái','Trên giữa','Trên phải'],1):self.alignment.addItem(name,i)
        form.addRow('Vị trí chữ',self.alignment)
        self.margin=QSpinBox();self.margin.setRange(0,32768);form.addRow('Lề dọc',self.margin)
        self.lines=QSpinBox();self.lines.setRange(1,4);form.addRow('Số dòng tối đa',self.lines)
        info=QLabel('Tọa độ theo video gốc. Chữ trắng, solid màu đen. Câu dài có thể tràn ngang: xem preview và chỉnh cỡ chữ/nội dung.');info.setWordWrap(True);form.addRow(info)
        self.output=QLabel('Chưa tạo preview.');self.output.setWordWrap(True);self.output.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse);outer.addWidget(self.output)
        self.canvas.selected.connect(self.set_rectangle);self.enabled.toggled.connect(self.update_mask);self.kind.currentTextChanged.connect(self.update_mask)
        self.player.errorOccurred.connect(lambda *args:self.output.setText('Không phát được preview: '+self.player.errorString()))
    def set_rectangle(self,x,y,w,h):
        for spin,value in zip(self.coords,(x,y,w,h)):spin.setValue(value)
        self.enabled.setChecked(True);self.update_mask()
    def values(self):
        return (Mask(self.enabled.isChecked(),self.kind.currentText(),*(s.value() for s in self.coords)),
            SubtitleStyle(self.font.currentText(),self.size.value(),self.bold.isChecked(),self.outline.value(),self.shadow.value(),self.alignment.currentData(),self.margin.value(),self.lines.value()))
    def update_mask(self,*args):
        if not self.loading:self.canvas.mask=self.values()[0];self.canvas.update()
    def load_project(self,project):
        self.loading=True;m,s=project.mask,project.subtitle_style
        self.time.setMaximum(max(0,float(project.metadata.get('duration',0))-.05))
        self.enabled.setChecked(m.enabled);self.kind.setCurrentText(m.kind)
        for spin,value in zip(self.coords,(m.x,m.y,m.width,m.height)):spin.setValue(value)
        self.font.setCurrentFont(QFont(s.font));self.font.setCurrentText(s.font);self.size.setValue(s.font_size);self.bold.setChecked(s.bold)
        self.outline.setValue(s.outline);self.shadow.setValue(s.shadow);self.alignment.setCurrentIndex(s.alignment-1);self.margin.setValue(s.margin_bottom);self.lines.setValue(s.max_lines)
        self.loading=False;self.update_mask()
    def reset_media(self):
        self.player.stop();self.player.setSource(QUrl());self.canvas.pixmap=QPixmap();self.canvas.update();self.output.setText('Lấy khung hình cho project đang mở.')
    def show_frame(self,path):
        self.canvas.pixmap=QPixmap(str(path));self.canvas.update();self.views.setCurrentIndex(0)
    def show_preview(self,path):
        self.player.stop();self.player.setSource(QUrl.fromLocalFile(str(path)));self.views.setCurrentIndex(1)
        self.output.setText('Preview đã tạo. Bấm Phát preview. '+str(path))
    def hideEvent(self,event):
        self.player.stop();super().hideEvent(event)


def build():return MaskStylePage()
