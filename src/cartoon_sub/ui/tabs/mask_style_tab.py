from PySide6.QtCore import Qt, QRectF, Signal, QUrl, QTimer
from PySide6.QtGui import QPixmap, QPainter, QColor, QPen, QFont, QPainterPath
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QFormLayout,QLabel,QPushButton,QAbstractSpinBox,
    QSpinBox,QDoubleSpinBox,QCheckBox,QComboBox,QTabWidget,QScrollArea,QFileDialog,QLineEdit,QSplitter,QColorDialog)
from PySide6.QtMultimedia import QMediaPlayer,QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget
from cartoon_sub.subtitle.models import Mask,SubtitleStyle,LogoOverlay,WatermarkStyle
from cartoon_sub.ui.undo import AppliedValueCommand
from copy import deepcopy


# These families include Vietnamese glyphs. The OS/FFmpeg resolves the installed
# one, so the list deliberately avoids fonts bundled only with this project.
VIETNAMESE_FONTS = (
    'Arial', 'Arial Unicode MS', 'Aptos', 'Bahnschrift', 'Calibri', 'Cambria',
    'Candara', 'Comic Sans MS', 'Consolas', 'Constantia', 'Corbel', 'Courier New',
    'Georgia', 'Noto Sans', 'Noto Serif', 'Open Sans', 'Palatino Linotype',
    'Roboto', 'Segoe UI', 'Tahoma', 'Times New Roman', 'Trebuchet MS', 'Verdana',
    'Be Vietnam Pro', 'Montserrat',
)


class ColorButton(QPushButton):
    colorChanged = Signal(str)
    def __init__(self, color, parent=None):
        super().__init__(parent);self._color='';self.clicked.connect(self.pick_color);self.set_color(color)
    def color(self): return self._color
    def set_color(self, color):
        value=QColor(color)
        if not value.isValid(): raise ValueError('Màu phải có định dạng #RRGGBB')
        normalized=value.name().upper()
        if normalized == self._color:return
        self._color=normalized;self.setText(normalized)
        contrast='#000000' if value.lightness()>150 else '#FFFFFF'
        self.setStyleSheet(f'QPushButton {{ background: {normalized}; color: {contrast}; border: 1px solid #777; padding: 4px; }}')
        self.colorChanged.emit(normalized)
    def pick_color(self):
        selected=QColorDialog.getColor(QColor(self._color),self,'Chọn màu')
        if selected.isValid():self.set_color(selected.name())


class MaskCanvas(QWidget):
    selected = Signal(int,int,int,int)
    logo_selected = Signal(str)
    logo_deleted = Signal(str)
    logo_moved = Signal(str,int,int)
    logo_drag_finished = Signal(str)
    def __init__(self):
        super().__init__();self.setMinimumSize(320,200)
        self.pixmap=QPixmap();self.mask=Mask();self.style=SubtitleStyle();self.watermark=WatermarkStyle();self.preview_time=0.;self.logos=[];self.selected_logo=None;self.origin=None;self.drag=None;self.logo_drag_offset=None
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.watermark_timer=QTimer(self);self.watermark_timer.setInterval(50);self.watermark_timer.timeout.connect(self.advance_watermark);self.watermark_timer.start()
    def advance_watermark(self):
        if self.watermark.text.strip():self.preview_time+=.05;self.update()
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
            px,py=self.point(e.position())
            for logo in reversed(self.logos):
                if logo.x <= px <= logo.x+logo.width and logo.y <= py <= logo.y+logo.height:
                    self.selected_logo=logo.id;self.logo_drag_offset=(px-logo.x,py-logo.y);self.logo_selected.emit(logo.id);self.setFocus();self.update();return
            self.origin=self.point(e.position());self.drag=None
    def mouseMoveEvent(self,e):
        if self.selected_logo and self.logo_drag_offset is not None:
            logo=next((item for item in self.logos if item.id==self.selected_logo),None)
            if logo:
                px,py=self.point(e.position());dx,dy=self.logo_drag_offset
                logo.x=max(0,min(max(0,self.pixmap.width()-logo.width),px-dx))
                logo.y=max(0,min(max(0,self.pixmap.height()-logo.height),py-dy))
                self.logo_moved.emit(logo.id,logo.x,logo.y);self.update()
            return
        if self.origin:
            p=self.point(e.position());x,y=self.origin
            self.drag=(min(x,p[0]),min(y,p[1]),abs(x-p[0]),abs(y-p[1]));self.update()
    def mouseReleaseEvent(self,e):
        if e.button()==Qt.MouseButton.LeftButton and self.logo_drag_offset is not None:
            logo_id=self.selected_logo;self.logo_drag_offset=None;self.update()
            if logo_id:self.logo_drag_finished.emit(logo_id)
            return
        if e.button()==Qt.MouseButton.LeftButton and self.origin:
            self.mouseMoveEvent(e)
            if self.drag and min(self.drag[2:])>=2:self.selected.emit(*self.drag)
            self.origin=None;self.drag=None;self.update()
    def draw_mask_effect(self,p,rect,x,y,w,h,kind,strength,mask_color='#000000'):
        if kind == 'none':
            return
        if kind == 'solid':
            p.fillRect(rect,QColor(mask_color));return
        source=self.pixmap.copy(max(0,x),max(0,y),max(1,min(w,self.pixmap.width()-max(0,x))),max(1,min(h,self.pixmap.height()-max(0,y))))
        if source.isNull():return
        # Fast canvas approximation follows the FFmpeg radius/sigma mapping.
        factor=max(2,round(strength*2.4))
        reduced=source.scaled(max(1,source.width()//factor),max(1,source.height()//factor),Qt.AspectRatioMode.IgnoreAspectRatio,Qt.TransformationMode.FastTransformation)
        p.drawPixmap(rect.toRect(),reduced.scaled(rect.size().toSize(),Qt.AspectRatioMode.IgnoreAspectRatio,Qt.TransformationMode.SmoothTransformation))
    def draw_preview_subtitle(self,p,r):
        style=self.style;text='Phụ đề mẫu tiếng Việt'
        if self.mask.width < 2 or self.mask.height < 2:return
        sx=r.width()/self.pixmap.width();sy=r.height()/self.pixmap.height();scale=min(sx,sy)
        font=QFont(style.font);font.setBold(style.bold);font.setPixelSize(max(8,round(style.font_size*scale)))
        path=QPainterPath();path.addText(0,0,font,text);bounds=path.boundingRect()
        cx=r.x()+(self.mask.x+self.mask.width/2)*sx;cy=r.y()+(self.mask.y+self.mask.height/2)*sy
        path.translate(cx-bounds.center().x(),cy-bounds.center().y())
        if style.shadow:
            shadow=QPainterPath(path);shadow.translate(style.shadow*scale,style.shadow*scale)
            p.fillPath(shadow,QColor(0,0,0,210))
        if style.outline:p.strokePath(path,QPen(QColor(style.outline_color),max(1.,style.outline*scale*2)))
        p.fillPath(path,QColor(style.text_color))
    @staticmethod
    def reflected_position(origin,velocity,time,maximum):
        if maximum <= 0:return 0.
        point=(origin+velocity*time)%(maximum*2)
        return point if point <= maximum else maximum*2-point
    def draw_preview_watermark(self,p,r):
        style=self.watermark;text=style.text.strip()
        if not text:return
        text_width=min(self.pixmap.width()-20,max(40,round(len(text)*style.font_size*.58)))
        text_height=max(24,round(style.font_size*1.35))
        x=self.reflected_position(20.,style.speed*.8,self.preview_time,self.pixmap.width()-text_width)
        y=self.reflected_position(20.,style.speed*.6,self.preview_time,self.pixmap.height()-text_height)
        sx=r.width()/self.pixmap.width();sy=r.height()/self.pixmap.height();scale=min(sx,sy)
        font=QFont(style.font);font.setBold(style.bold);font.setPixelSize(max(8,round(style.font_size*scale)));p.setFont(font)
        path=QPainterPath();path.addText(r.x()+x*sx,r.y()+y*sy+p.fontMetrics().ascent(),font,text)
        p.save();p.setOpacity(1-style.transparency/100)
        shadow=max(0.,style.shadow*scale);outline=max(0.,style.outline*scale)
        if shadow:
            shadow_path=QPainterPath();shadow_path.addText(r.x()+(x+style.shadow)*sx,r.y()+(y+style.shadow)*sy+p.fontMetrics().ascent(),font,text)
            p.fillPath(shadow_path,QColor(0,0,0,210))
        if outline:p.strokePath(path,QPen(QColor(0,0,0,230),max(1.,outline*2)))
        p.fillPath(path,QColor('white'));p.restore()
    def paintEvent(self,e):
        p=QPainter(self);p.fillRect(self.rect(),QColor('#20252b'));r=self.image_rect()
        if r.isEmpty():
            p.setPen(Qt.GlobalColor.white);p.drawText(self.rect(),Qt.AlignmentFlag.AlignCenter,'Lấy khung hình rồi kéo vùng che phụ đề Trung.');return
        p.drawPixmap(r.toRect(),self.pixmap);m=self.mask
        box=self.drag or ((m.x,m.y,m.width,m.height) if m.enabled else None)
        if box and m.kind != 'none':
            x,y,w,h=box;sx=r.width()/self.pixmap.width();sy=r.height()/self.pixmap.height()
            rect=QRectF(r.x()+x*sx,r.y()+y*sy,w*sx,h*sy)
            self.draw_mask_effect(p,rect,x,y,w,h,m.kind,m.strength,m.mask_color)
            p.setPen(QPen(QColor('#00baff'),2));p.drawRect(rect)
        for logo in self.logos:
            image=QPixmap(logo.path)
            if image.isNull(): continue
            target=QRectF(r.x()+logo.x*r.width()/self.pixmap.width(),r.y()+logo.y*r.height()/self.pixmap.height(),logo.width*r.width()/self.pixmap.width(),logo.height*r.height()/self.pixmap.height())
            p.save();p.setOpacity(1-logo.transparency/100);p.translate(target.center());p.rotate(logo.rotation)
            p.drawPixmap(QRectF(-target.width()/2,-target.height()/2,target.width(),target.height()),image,QRectF(image.rect()));p.restore()
            if logo.id==self.selected_logo: p.setPen(QPen(QColor('#ffff00'),2));p.drawRect(target)
        self.draw_preview_subtitle(p,r)
        self.draw_preview_watermark(p,r)
    def keyPressEvent(self,e):
        if e.key()==Qt.Key.Key_Delete and self.selected_logo:
            self.logo_deleted.emit(self.selected_logo);return
        super().keyPressEvent(e)


class MaskStylePage(QWidget):
    def __init__(self):
        super().__init__();self.loading=False;self.undo_stack=None;self.undo_commit=None;self._project=None;self._committed_visual=None;self._committed_watermark=None;self._committed_logos={}
        outer=QVBoxLayout(self)
        note=QLabel('Lấy khung hình → Kéo vùng che → Chỉnh chữ → Tạo preview → Render MP4. Dùng bản VI Subtitle; xử lý local.');note.setWordWrap(True);outer.addWidget(note)
        bar=QHBoxLayout();self.time=QDoubleSpinBox();self.time.setDecimals(2);self.time.setSuffix(' s');bar.addWidget(self.time)
        for name,label in [('frame_button','Lấy khung hình'),('preview_button','Tạo preview 10 giây'),('render_button','Render toàn bộ MP4'),('save_button','Lưu Mask / Style')]:
            b=QPushButton(label);setattr(self,name,b);bar.addWidget(b)
        outer.addLayout(bar);body=QHBoxLayout();outer.addLayout(body,1)
        self.views=QTabWidget();self.canvas=MaskCanvas();self.views.addTab(self.canvas,'Chọn vùng che')
        playback=QWidget();pv=QVBoxLayout(playback);self.video=QVideoWidget();pv.addWidget(self.video,1)
        buttons=QHBoxLayout();self.play_button=QPushButton('Phát preview');self.stop_button=QPushButton('Dừng');buttons.addWidget(self.play_button);buttons.addWidget(self.stop_button);pv.addLayout(buttons)
        self.views.addTab(playback,'Video preview')
        self.player=QMediaPlayer(self);self.audio=QAudioOutput(self);self.player.setAudioOutput(self.audio);self.player.setVideoOutput(self.video)
        self.play_button.clicked.connect(self.player.play);self.stop_button.clicked.connect(self.player.stop)
        panel=QWidget();form=QFormLayout(panel);scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setWidget(panel)
        scroll.setMinimumWidth(300);scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded);scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        splitter=QSplitter(Qt.Orientation.Horizontal);splitter.addWidget(self.views);splitter.addWidget(scroll);splitter.setStretchFactor(0,1);splitter.setSizes([900,360]);body.addWidget(splitter,1)
        self.options_scroll=scroll;self.options_splitter=splitter
        self.enabled=QCheckBox('Bật vùng che');form.addRow(self.enabled)
        self.kind=QComboBox();self.kind.addItem('Solid','solid');self.kind.addItem('Gaussian','gaussian');self.kind.addItem('No mask','none');form.addRow('Kiểu mask',self.kind)
        self.mask_color=ColorButton('#000000');form.addRow('Màu khung mask',self.mask_color)
        self.strength=QSpinBox();self.strength.setRange(1,20);self.strength.setValue(12);form.addRow('Độ nhòe (1–20)',self.strength)
        self.coords=[]
        for label in ('X','Y','Width','Height'):
            spin=QSpinBox();spin.setRange(0,32768);form.addRow(label,spin);self.coords.append(spin);spin.valueChanged.connect(self.update_mask)
        self.font=QComboBox();self.font.addItems(VIETNAMESE_FONTS);form.addRow('Font',self.font)
        self.size=QSpinBox();self.size.setRange(8,300);form.addRow('Cỡ chữ (pixel)',self.size)
        self.bold=QCheckBox();form.addRow('Đậm',self.bold)
        self.text_color=ColorButton('#FFFFFF');form.addRow('Màu chữ',self.text_color)
        self.outline=QDoubleSpinBox();self.outline.setRange(0,20);form.addRow('Viền',self.outline)
        self.outline_color=ColorButton('#000000');form.addRow('Màu viền chữ',self.outline_color)
        self.shadow=QDoubleSpinBox();self.shadow.setRange(0,20);form.addRow('Bóng',self.shadow)
        self.margin=QSpinBox();self.margin.setRange(0,32768);form.addRow('Lề dọc',self.margin)
        self.speaker_label_mode=QComboBox()
        self.speaker_label_mode.addItem('Chỉ khi overlap (mặc định)','overlap_only')
        self.speaker_label_mode.addItem('Luôn hiện tên','always')
        self.speaker_label_mode.addItem('Tắt','off')
        self.speaker_label_mode.addItem('Debug timing','debug')
        form.addRow('Nhãn nhân vật',self.speaker_label_mode)
        info=QLabel('Khi bật, phụ đề nằm chính giữa vùng solid/blur đã chọn. Tọa độ theo video gốc.');info.setWordWrap(True);form.addRow(info)
        self.logo=QComboBox();self.load_logo=QPushButton('Load logo ảnh');self.remove_logo=QPushButton('Xóa logo chọn')
        logo_buttons=QHBoxLayout();logo_buttons.addWidget(self.load_logo);logo_buttons.addWidget(self.remove_logo);form.addRow('Logo',self.logo);form.addRow(logo_buttons)
        self.logo_controls=[]
        for label,low,high in [('Logo X',0,32768),('Logo Y',0,32768),('Logo rộng',2,32768),('Logo cao',2,32768),('Xoay logo',-360,360),('Trong suốt logo %',0,100)]:
            spin=QSpinBox();spin.setRange(low,high);form.addRow(label,spin);self.logo_controls.append(spin)
        self.logo_scale=QSpinBox();self.logo_scale.setRange(-100000,100000);self.logo_scale.setValue(100);self.logo_scale.setSuffix(' %');self.logo_scale.setKeyboardTracking(False);self.logo_scale.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons);form.addRow('Logo Scale',self.logo_scale)
        self.watermark_text=QLineEdit();form.addRow('Watermark text',self.watermark_text)
        self.watermark_font=QComboBox();self.watermark_font.addItems(VIETNAMESE_FONTS);form.addRow('Font watermark',self.watermark_font)
        self.watermark_size=QSpinBox();self.watermark_size.setRange(8,300);form.addRow('Cỡ watermark',self.watermark_size)
        self.watermark_bold=QCheckBox();form.addRow('Đậm watermark',self.watermark_bold)
        self.watermark_outline=QDoubleSpinBox();self.watermark_outline.setRange(0,20);form.addRow('Viền watermark',self.watermark_outline)
        self.watermark_shadow=QDoubleSpinBox();self.watermark_shadow.setRange(0,20);form.addRow('Bóng watermark',self.watermark_shadow)
        self.watermark_transparency=QSpinBox();self.watermark_transparency.setRange(0,100);form.addRow('Trong suốt watermark %',self.watermark_transparency)
        self.watermark_speed=QSpinBox();self.watermark_speed.setRange(10,1000);form.addRow('Tốc độ watermark',self.watermark_speed)
        self.output=QLabel('Chưa tạo preview.');self.output.setWordWrap(True);self.output.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse);outer.addWidget(self.output)
        self.canvas.selected.connect(self.set_rectangle);self.enabled.toggled.connect(self.update_mask);self.kind.currentTextChanged.connect(self.update_mask);self.strength.valueChanged.connect(self.update_mask)
        self.mask_color.colorChanged.connect(self.update_mask);self.text_color.colorChanged.connect(self.update_mask);self.outline_color.colorChanged.connect(self.update_mask)
        self.load_logo.clicked.connect(self.add_logo);self.remove_logo.clicked.connect(self.delete_logo);self.logo.currentIndexChanged.connect(self.load_selected_logo)
        self.canvas.logo_selected.connect(self.select_logo);self.canvas.logo_deleted.connect(self.delete_logo);self.canvas.logo_moved.connect(self.logo_moved);self.canvas.logo_drag_finished.connect(self.commit_logo_drag)
        for control in self.logo_controls:
            control.valueChanged.connect(self.update_logo);control.editingFinished.connect(self.commit_logo_controls)
        self.logo_scale.editingFinished.connect(self.update_logo_scale)
        for signal in (self.font.currentTextChanged,self.size.valueChanged,self.bold.toggled,self.outline.valueChanged,
                       self.shadow.valueChanged,self.margin.valueChanged,
                       self.speaker_label_mode.currentIndexChanged): signal.connect(self.update_mask)
        for signal in (self.watermark_text.textChanged,self.watermark_font.currentTextChanged,self.watermark_size.valueChanged,
                       self.watermark_bold.toggled,self.watermark_outline.valueChanged,self.watermark_shadow.valueChanged,
                       self.watermark_transparency.valueChanged,self.watermark_speed.valueChanged): signal.connect(self.update_mask)
        self.watermark_text.editingFinished.connect(self.commit_watermark_state)
        for spin in (self.watermark_size,self.watermark_outline,self.watermark_shadow,self.watermark_transparency,self.watermark_speed):
            spin.setKeyboardTracking(False);spin.editingFinished.connect(self.commit_watermark_state)
        for signal in (self.watermark_font.currentIndexChanged,self.watermark_bold.toggled):signal.connect(self.commit_watermark_state)
        self.player.errorOccurred.connect(lambda *args:self.output.setText('Không phát được preview: '+self.player.errorString()))
        for spin in (*self.coords,self.strength,self.size,self.outline,self.shadow,self.margin):
            spin.setKeyboardTracking(False);spin.editingFinished.connect(self.commit_visual_state)
        for signal in (self.enabled.toggled,self.kind.currentIndexChanged,self.font.currentIndexChanged,self.bold.toggled,
                       self.speaker_label_mode.currentIndexChanged,
                       self.mask_color.colorChanged,self.text_color.colorChanged,self.outline_color.colorChanged):
            signal.connect(self.commit_visual_state)
    def set_undo_stack(self,stack,commit=None):
        self.undo_stack,self.undo_commit=stack,commit
    def set_rectangle(self,x,y,w,h):
        for spin,value in zip(self.coords,(x,y,w,h)):spin.setValue(value)
        self.enabled.setChecked(True);self.update_mask();self.commit_visual_state()
    def values(self):
        legacy_max_lines=getattr(getattr(self._project,'subtitle_style',None),'max_lines',2)
        return (Mask(self.enabled.isChecked(),self.kind.currentData(),*(s.value() for s in self.coords),self.strength.value(),self.mask_color.color()),
            SubtitleStyle(self.font.currentText(),self.size.value(),self.bold.isChecked(),self.outline.value(),self.shadow.value(),5,self.margin.value(),legacy_max_lines,True,self.speaker_label_mode.currentData() or 'overlap_only',self.text_color.color(),self.outline_color.color()))
    def overlay_values(self):
        return list(self.canvas.logos), WatermarkStyle(self.watermark_text.text(),self.watermark_font.currentText(),
            self.watermark_size.value(),self.watermark_bold.isChecked(),self.watermark_outline.value(),self.watermark_shadow.value(),
            self.watermark_transparency.value(),self.watermark_speed.value())
    def add_logo(self):
        path,_=QFileDialog.getOpenFileName(self,'Chọn logo','','Images (*.png *.jpg *.jpeg *.webp *.bmp)')
        if not path:return
        image=QPixmap(path)
        if image.isNull():return
        before=deepcopy(self.canvas.logos)
        ratio=image.height()/max(1,image.width());width=160;height=max(2,round(width*ratio))
        logo=LogoOverlay(str(len(self.canvas.logos)+1),path,40,40,width,height,base_width=width,base_height=height,scale_percent=100)
        self.canvas.logos.append(logo);self.logo.addItem(f'Logo {logo.id}',logo.id);self.logo.setCurrentIndex(self.logo.count()-1);self.canvas.selected_logo=logo.id;self.canvas.update()
        self._committed_logos[logo.id]=deepcopy(vars(logo))
        self._record_logo_list_change(before,'Add logo')
    def select_logo(self,logo_id):
        index=self.logo.findData(logo_id)
        if index>=0:self.logo.setCurrentIndex(index)
    def load_selected_logo(self,*args):
        logo=next((item for item in self.canvas.logos if item.id==self.logo.currentData()),None)
        if not logo:return
        percent=logo.scale_percent if logo.scale_percent is not None else 100+logo.scale
        magnitude=abs(percent)/100
        if logo.base_width is None:logo.base_width=max(2,round(logo.width/max(magnitude,.0001)))
        if logo.base_height is None:logo.base_height=max(2,round(logo.height/max(magnitude,.0001)))
        for spin,value in zip(self.logo_controls,(logo.x,logo.y,logo.width,logo.height,round(logo.rotation),logo.transparency)):
            spin.blockSignals(True);spin.setValue(value);spin.blockSignals(False)
        self.logo_scale.blockSignals(True);self.logo_scale.setValue(percent);self.logo_scale.blockSignals(False)
        self.canvas.selected_logo=logo.id;self.canvas.update()
    def update_logo(self,*args):
        logo=next((item for item in self.canvas.logos if item.id==self.logo.currentData()),None)
        if not logo:return
        logo.x,logo.y,logo.width,logo.height,logo.rotation,logo.transparency=(spin.value() for spin in self.logo_controls)
        magnitude=abs(logo.scale_percent if logo.scale_percent is not None else 100+logo.scale)/100
        if magnitude>0:logo.base_width=max(2,round(logo.width/magnitude));logo.base_height=max(2,round(logo.height/magnitude))
        self.canvas.update()
    def commit_logo_controls(self):
        logo=next((item for item in self.canvas.logos if item.id==self.logo.currentData()),None)
        if not logo:return
        old=self._committed_logos.get(logo.id);new=deepcopy(vars(logo))
        if old and old!=new and self.undo_stack:
            self.undo_stack.push(AppliedValueCommand('Edit logo',old,new,lambda state,lid=logo.id:self._apply_logo_state(lid,state)))
        self._committed_logos[logo.id]=new
    def update_logo_scale(self,*args):
        logo=next((item for item in self.canvas.logos if item.id==self.logo.currentData()),None)
        if not logo:return
        if logo.base_width is None:logo.base_width=logo.width
        if logo.base_height is None:logo.base_height=logo.height
        old=self._committed_logos.get(logo.id,deepcopy(vars(logo)))
        logo.scale_percent=self.logo_scale.value();factor=abs(logo.scale_percent)/100
        logo.width=max(2,round(logo.base_width*factor));logo.height=max(2,round(logo.base_height*factor))
        for spin,value in zip(self.logo_controls[2:4],(logo.width,logo.height)):
            spin.blockSignals(True);spin.setValue(value);spin.blockSignals(False)
        self.canvas.update()
        new=deepcopy(vars(logo));self._committed_logos[logo.id]=new
        if old!=new and self.undo_stack:
            self.undo_stack.push(AppliedValueCommand('Logo scale',old,new,lambda state,lid=logo.id:self._apply_logo_state(lid,state)))
    def _apply_logo_state(self,logo_id,state):
        logo=next((item for item in self.canvas.logos if item.id==logo_id),None)
        if not logo:return
        for key,value in state.items():setattr(logo,key,deepcopy(value))
        self._committed_logos[logo_id]=deepcopy(state);self.select_logo(logo_id);self.load_selected_logo();self.canvas.update()
        if self.undo_commit:self.undo_commit()
    def logo_moved(self,logo_id,x,y):
        index=self.logo.findData(logo_id)
        if index >= 0 and index != self.logo.currentIndex(): self.logo.setCurrentIndex(index)
        for spin,value in zip(self.logo_controls[:2],(x,y)):
            spin.blockSignals(True);spin.setValue(value);spin.blockSignals(False)
    def commit_logo_drag(self,logo_id):
        self.select_logo(logo_id);self.commit_logo_controls()
    def delete_logo(self,logo_id=None):
        before=deepcopy(self.canvas.logos)
        logo_id=logo_id or self.logo.currentData()
        self.canvas.logos=[item for item in self.canvas.logos if item.id!=logo_id]
        index=self.logo.findData(logo_id)
        if index>=0:self.logo.removeItem(index)
        self.canvas.selected_logo=None;self.canvas.update()
        self._record_logo_list_change(before,'Delete logo')
    def _record_logo_list_change(self,before,text):
        after=deepcopy(self.canvas.logos)
        if before!=after and self.undo_stack:
            self.undo_stack.push(AppliedValueCommand(text,before,after,self._apply_logo_list))
    def _apply_logo_list(self,logos):
        self.canvas.logos=deepcopy(logos);self.logo.blockSignals(True);self.logo.clear();self._committed_logos={}
        for logo in self.canvas.logos:
            self.logo.addItem(f'Logo {logo.id}',logo.id);self._committed_logos[logo.id]=deepcopy(vars(logo))
        self.logo.blockSignals(False);self.canvas.selected_logo=None;self.canvas.update()
        if self._project:self._project.logos=self.canvas.logos
        if self.undo_commit:self.undo_commit()
    def update_mask(self,*args):
        if not self.loading:
            self.canvas.mask,self.canvas.style=self.values();self.canvas.update()
            self.canvas.watermark=self.overlay_values()[1]
            has_mask = self.kind.currentData() != 'none'
            self.mask_color.setEnabled(has_mask)
            self.strength.setEnabled(has_mask and self.kind.currentData() != 'solid')
    def commit_watermark_state(self,*args):
        if self.loading:return
        state=deepcopy(self.overlay_values()[1]);old=self._committed_watermark
        if self._project:self._project.watermark=deepcopy(state)
        self._committed_watermark=state
        if old and old!=state and self.undo_stack:
            self.undo_stack.push(AppliedValueCommand('Edit watermark',old,state,self._apply_watermark_state))
    def _apply_watermark_state(self,state):
        state=deepcopy(state);self.loading=True
        if self.watermark_font.findText(state.font)<0:self.watermark_font.insertItem(0,state.font)
        self.watermark_text.setText(state.text);self.watermark_font.setCurrentText(state.font);self.watermark_size.setValue(state.font_size);self.watermark_bold.setChecked(state.bold);self.watermark_outline.setValue(state.outline);self.watermark_shadow.setValue(state.shadow);self.watermark_transparency.setValue(state.transparency);self.watermark_speed.setValue(state.speed)
        self.loading=False;self.update_mask();self._committed_watermark=state
        if self._project:self._project.watermark=deepcopy(state)
        if self.undo_commit:self.undo_commit()
    def commit_visual_state(self,*args):
        if self.loading:return
        state=tuple(deepcopy(value) for value in self.values())
        old=self._committed_visual
        if self._project:self._project.mask,self._project.subtitle_style=deepcopy(state)
        self._committed_visual=state
        if old and old!=state and self.undo_stack:
            self.undo_stack.push(AppliedValueCommand('Edit mask/style',old,state,self._apply_visual_state))
    def _apply_visual_state(self,state):
        m,s=deepcopy(state);self.loading=True
        self.enabled.setChecked(m.enabled);self.kind.setCurrentIndex(max(0,self.kind.findData(m.kind)));self.mask_color.set_color(m.mask_color);self.strength.setValue(m.strength)
        for spin,value in zip(self.coords,(m.x,m.y,m.width,m.height)):spin.setValue(value)
        if self.font.findText(s.font)<0:self.font.insertItem(0,s.font)
        self.font.setCurrentText(s.font);self.size.setValue(s.font_size);self.bold.setChecked(s.bold);self.text_color.set_color(s.text_color);self.outline.setValue(s.outline);self.outline_color.set_color(s.outline_color);self.shadow.setValue(s.shadow);self.margin.setValue(s.margin_bottom);self.speaker_label_mode.setCurrentIndex(max(0,self.speaker_label_mode.findData(s.speaker_label_mode)))
        self.loading=False;self.update_mask();self._committed_visual=(deepcopy(m),deepcopy(s))
        if self._project:self._project.mask,self._project.subtitle_style=deepcopy((m,s))
        if self.undo_commit:self.undo_commit()
    def load_project(self,project):
        self.loading=True;self._project=project;m,s=project.mask,project.subtitle_style
        self.time.setMaximum(max(0,float(project.metadata.get('duration',0))-.05))
        self.enabled.setChecked(m.enabled)
        index=self.kind.findData(m.kind)
        self.kind.setCurrentIndex(index if index >= 0 else self.kind.findText(m.kind))
        self.mask_color.set_color(m.mask_color)
        self.strength.setValue(m.strength)
        for spin,value in zip(self.coords,(m.x,m.y,m.width,m.height)):spin.setValue(value)
        if self.font.findText(s.font) < 0:self.font.insertItem(0,s.font)
        self.font.setCurrentText(s.font);self.size.setValue(s.font_size);self.bold.setChecked(s.bold)
        self.text_color.set_color(s.text_color);self.outline_color.set_color(s.outline_color)
        self.outline.setValue(s.outline);self.shadow.setValue(s.shadow);self.margin.setValue(s.margin_bottom)
        mode_idx = self.speaker_label_mode.findData(getattr(s, 'speaker_label_mode', 'overlap_only'))
        if mode_idx >= 0: self.speaker_label_mode.setCurrentIndex(mode_idx)
        self.canvas.logos=list(project.logos);self.logo.clear()
        for logo in self.canvas.logos:
            self.logo.addItem(f'Logo {logo.id}',logo.id);self._committed_logos[logo.id]=deepcopy(vars(logo))
        watermark=project.watermark
        if self.watermark_font.findText(watermark.font)<0:self.watermark_font.insertItem(0,watermark.font)
        self.watermark_text.setText(watermark.text);self.watermark_font.setCurrentText(watermark.font);self.watermark_size.setValue(watermark.font_size);self.watermark_bold.setChecked(watermark.bold);self.watermark_outline.setValue(watermark.outline);self.watermark_shadow.setValue(watermark.shadow);self.watermark_transparency.setValue(watermark.transparency);self.watermark_speed.setValue(watermark.speed)
        self.loading=False;self.update_mask();self._committed_visual=tuple(deepcopy(value) for value in self.values());self._committed_watermark=deepcopy(self.overlay_values()[1])
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
