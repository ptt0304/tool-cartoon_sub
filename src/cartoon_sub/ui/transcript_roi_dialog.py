import re

from PySide6.QtCore import Qt, QRectF, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget
from cartoon_sub.transcription.video.roi import DEFAULT_VIDEO_SUBTITLE_ROI, clamp_roi, image_rect, normalized_to_display
from cartoon_sub.transcription.video.regions import normalize_regions

def _time_label(value):
    milliseconds=max(0,round(float(value)*1000)); hours,rest=divmod(milliseconds//1000,3600); minutes,seconds=divmod(rest,60); return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{milliseconds%1000:03d}"


_HMS_PATTERN = re.compile(r"^(\d+)h([0-5]\d)m([0-5]\d)s$")


def format_region_time(seconds):
    total = max(0, int(round(float(seconds))))
    hours, rest = divmod(total, 3600); minutes, seconds = divmod(rest, 60)
    return f"{hours:02d}h{minutes:02d}m{seconds:02d}s"


def parse_region_time(value):
    match = _HMS_PATTERN.fullmatch(str(value).strip())
    if not match:
        raise ValueError("Thời gian phải theo dạng HHhMMmSSs; phút và giây từ 00 đến 59.")
    hours, minutes, seconds = (int(part) for part in match.groups())
    return float(hours * 3600 + minutes * 60 + seconds)


class RegionTimeEdit(QLineEdit):
    """Editable whole-second UI field; the project remains numeric seconds."""
    valueChanged = Signal(float)
    invalidTime = Signal(str)

    def __init__(self, value=0.0, parent=None):
        super().__init__(parent); self._minimum = 0.0; self._maximum = float("inf"); self._seconds = 0.0
        self.editingFinished.connect(self._commit); self.setValue(value)

    def setRange(self, minimum, maximum):
        self._minimum, self._maximum = float(minimum), float(maximum); self.setValue(self._seconds)

    def value(self): return self._seconds

    def setValue(self, value):
        value = min(self._maximum, max(self._minimum, float(value)))
        changed = value != self._seconds; self._seconds = value; self.setText(format_region_time(value)); self.setStyleSheet("")
        if changed: self.valueChanged.emit(value)

    def _commit(self):
        try:
            value = parse_region_time(self.text())
            if not self._minimum <= value <= self._maximum:
                raise ValueError(f"Thời gian phải từ {format_region_time(self._minimum)} đến {format_region_time(self._maximum)}.")
        except ValueError as exc:
            self.setStyleSheet("border: 1px solid #c62828;"); self.invalidTime.emit(str(exc)); return
        self.setValue(value)

class RoiCanvas(QWidget):
    changed=Signal(dict)
    HANDLE_RADIUS = 7
    MINIMUM_ROI_PIXELS = 24
    def __init__(self,path,roi):
        super().__init__();self.setMinimumSize(640,360)
        self.pixmap = path if isinstance(path, QPixmap) else QPixmap(str(path))
        self.roi=clamp_roi(roi);self.drag=None
    def _image_rect(self):
        x,y,w,h=image_rect(self.width(),self.height(),self.pixmap.width(),self.pixmap.height());return QRectF(x,y,w,h)
    def _rect(self): return QRectF(*normalized_to_display(self.roi,tuple(self._image_rect().getRect())))
    def paintEvent(self,event):
        painter=QPainter(self);painter.fillRect(self.rect(),QColor('#20252b'));rect=self._image_rect();painter.drawPixmap(rect.toRect(),self.pixmap);roi=self._rect();painter.fillRect(roi,QColor(0,180,255,45));painter.setPen(QPen(QColor('#00baff'),2));painter.drawRect(roi)
        painter.setBrush(QColor('#ffffff')); painter.setPen(QPen(QColor('#00baff'),1))
        for point in (roi.topLeft(), roi.topRight(), roi.bottomLeft(), roi.bottomRight()):
            painter.drawRect(QRectF(point.x()-self.HANDLE_RADIUS, point.y()-self.HANDLE_RADIUS, self.HANDLE_RADIUS*2, self.HANDLE_RADIUS*2))
        painter.setPen(Qt.GlobalColor.white);painter.drawText(roi.adjusted(6,6,-6,-6),Qt.AlignmentFlag.AlignTop,'Vùng phụ đề')

    def _hit_test(self, point):
        rect, radius = self._rect(), self.HANDLE_RADIUS + 3
        corners = (("top_left", rect.topLeft()), ("top_right", rect.topRight()),
                   ("bottom_left", rect.bottomLeft()), ("bottom_right", rect.bottomRight()))
        for name, corner in corners:
            if abs(point.x()-corner.x()) <= radius and abs(point.y()-corner.y()) <= radius:
                return name
        if rect.top() <= point.y() <= rect.bottom():
            if abs(point.x()-rect.left()) <= radius: return "left"
            if abs(point.x()-rect.right()) <= radius: return "right"
        if rect.left() <= point.x() <= rect.right():
            if abs(point.y()-rect.top()) <= radius: return "top"
            if abs(point.y()-rect.bottom()) <= radius: return "bottom"
        return "move" if rect.contains(point) else None

    def _cursor_for(self, action):
        return {
            "top_left": Qt.CursorShape.SizeFDiagCursor, "bottom_right": Qt.CursorShape.SizeFDiagCursor,
            "top_right": Qt.CursorShape.SizeBDiagCursor, "bottom_left": Qt.CursorShape.SizeBDiagCursor,
            "left": Qt.CursorShape.SizeHorCursor, "right": Qt.CursorShape.SizeHorCursor,
            "top": Qt.CursorShape.SizeVerCursor, "bottom": Qt.CursorShape.SizeVerCursor,
            "move": Qt.CursorShape.SizeAllCursor,
        }.get(action, Qt.CursorShape.ArrowCursor)
    def mousePressEvent(self,event):
        if event.button()!=Qt.MouseButton.LeftButton or not self._image_rect().contains(event.position()):return
        action=self._hit_test(event.position())
        if action:self.drag=(action,event.position(),dict(self.roi))
    def mouseReleaseEvent(self,event):self.drag=None;self.setCursor(Qt.CursorShape.ArrowCursor)
    def mouseMoveEvent(self,event):
        if self.drag:
            kind,origin,initial=self.drag;image=self._image_rect();dx=(event.position().x()-origin.x())/image.width();dy=(event.position().y()-origin.y())/image.height();self.roi=self._apply_drag(initial,kind,dx,dy);self.changed.emit(dict(self.roi));self.update();return
        self.setCursor(self._cursor_for(self._hit_test(event.position())))

    def _apply_drag(self, initial, action, dx, dy):
        x,y,w,h=(float(initial[key]) for key in ('x_pct','y_pct','w_pct','h_pct'))
        minimum_w=self.MINIMUM_ROI_PIXELS/max(1.0,self._image_rect().width());minimum_h=self.MINIMUM_ROI_PIXELS/max(1.0,self._image_rect().height())
        if action=='move': x=min(1-w,max(0,x+dx)); y=min(1-h,max(0,y+dy)); return clamp_roi({'x_pct':x,'y_pct':y,'w_pct':w,'h_pct':h})
        if 'left' in action: x+=dx;w-=dx
        if 'right' in action: w+=dx
        if 'top' in action: y+=dy;h-=dy
        if 'bottom' in action: h+=dy
        if w<minimum_w:
            if 'left' in action:x=initial['x_pct']+initial['w_pct']-minimum_w
            w=minimum_w
        if h<minimum_h:
            if 'top' in action:y=initial['y_pct']+initial['h_pct']-minimum_h
            h=minimum_h
        if x<0: w+=x if 'left' in action else 0;x=max(0,x)
        if y<0: h+=y if 'top' in action else 0;y=max(0,y)
        if x+w>1:
            if 'right' in action:w=1-x
            else:x=1-w
        if y+h>1:
            if 'bottom' in action:h=1-y
            else:y=1-h
        return clamp_roi({'x_pct':x,'y_pct':y,'w_pct':max(minimum_w,w),'h_pct':max(minimum_h,h)})
    def set_roi(self,roi,*,emit=True):
        self.roi=clamp_roi(roi)
        if emit:self.changed.emit(dict(self.roi))
        self.update()
    def set_preview(self,path):self.pixmap=QPixmap(str(path));self.update()


class MaskRoiDialog(QDialog):
    """Single-region Mask editor backed by the same normalized RoiCanvas as Transcript."""
    def __init__(self, preview, roi, parent=None, *, default_roi=None):
        super().__init__(parent); self.setWindowTitle('Chọn vùng phụ đề / Mask'); self.resize(960, 680)
        self.default_roi = clamp_roi(default_roi or roi or DEFAULT_VIDEO_SUBTITLE_ROI)
        layout = QVBoxLayout(self)
        note = QLabel('Kéo vùng để di chuyển; kéo cạnh hoặc góc để đổi kích thước. Vùng chỉ xác định vị trí phụ đề/Mask, không thay đổi kiểu Mask.')
        note.setWordWrap(True); layout.addWidget(note)
        self.canvas = RoiCanvas(preview, roi or self.default_roi); layout.addWidget(self.canvas, 1)
        actions = QHBoxLayout(); default = QPushButton('ROI mặc định dưới giữa')
        default.clicked.connect(lambda: self.canvas.set_roi(self.default_roi))
        actions.addWidget(default); actions.addStretch(1); layout.addLayout(actions)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); layout.addWidget(buttons)

    def value(self):
        return dict(self.canvas.roi)

class TranscriptRoiDialog(QDialog):
    """Static original-video frame preview and unsaved temporal region edits."""
    def __init__(self,preview_path,regions_or_roi,parent=None,*,duration=0.0,preview_at=None):
        super().__init__(parent);self.setWindowTitle('Các vùng phụ đề Video');self.resize(1040,760);self.duration=max(0.0,float(duration or 0.0));self.preview_at=preview_at;self.current_time=0.0;self._syncing=False
        if isinstance(regions_or_roi,dict):self.regions=[] if not regions_or_roi or self.duration<=0 else [{'id':'region_001','start_time':0.0,'end_time':self.duration,'roi':clamp_roi(regions_or_roi)}]
        else:self.regions=[dict(row,roi=clamp_roi(row['roi'])) for row in (regions_or_roi or [])]
        self.selected_index=0 if self.regions else -1;layout=QVBoxLayout(self);note=QLabel('Nhập mốc Start/End từ video đang xem bên ngoài. Bấm xem trước để trích đúng một khung hình gốc, rồi kéo vùng phụ đề. Thời gian các vùng không được chồng nhau.');note.setWordWrap(True);layout.addWidget(note);self.canvas=RoiCanvas(preview_path,self._selected_roi());layout.addWidget(self.canvas,1);self.canvas.changed.connect(self._roi_changed)
        row=QHBoxLayout();row.addWidget(QLabel('Thời điểm xem trước'));self.time_spin=RegionTimeEdit();self.time_spin.setRange(0.0,self.duration);row.addWidget(self.time_spin);preview=QPushButton('Xem trước tại thời điểm này');self.set_start=QPushButton('Đặt Start từ thời điểm này');self.set_end=QPushButton('Đặt End từ thời điểm này');row.addWidget(preview);row.addWidget(self.set_start);row.addWidget(self.set_end);row.addStretch(1);layout.addLayout(row);self.current_label=QLabel();layout.addWidget(self.current_label)
        self.table=QTableWidget(0,5);self.table.setHorizontalHeaderLabels(['Vùng','Start','End','Duration','ROI']);self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows);self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers);self.table.itemSelectionChanged.connect(self._select_row);layout.addWidget(self.table)
        direct=QHBoxLayout();direct.addWidget(QLabel('Sửa trực tiếp Start'));self.start_spin=RegionTimeEdit();self.start_spin.setRange(0.0,self.duration);direct.addWidget(self.start_spin);direct.addWidget(QLabel('End'));self.end_spin=RegionTimeEdit();self.end_spin.setRange(0.0,self.duration);direct.addWidget(self.end_spin);direct.addStretch(1);layout.addLayout(direct)
        actions=QHBoxLayout();add=QPushButton('Thêm vùng');delete=QPushButton('Xóa vùng chọn');default=QPushButton('ROI mặc định dưới giữa');actions.addWidget(add);actions.addWidget(delete);actions.addWidget(default);actions.addStretch(1);layout.addLayout(actions);self.status=QLabel();self.status.setWordWrap(True);layout.addWidget(self.status);buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel);buttons.accepted.connect(self._confirm);buttons.rejected.connect(self.reject);layout.addWidget(buttons)
        self.time_spin.valueChanged.connect(self._time_changed);self.time_spin.invalidTime.connect(self._set_status);self.start_spin.invalidTime.connect(self._set_status);self.end_spin.invalidTime.connect(self._set_status);preview.clicked.connect(self._preview_current);self.set_start.clicked.connect(lambda:self._set_boundary('start_time'));self.set_end.clicked.connect(lambda:self._set_boundary('end_time'));self.start_spin.valueChanged.connect(lambda value:self._edit_boundary('start_time',value));self.end_spin.valueChanged.connect(lambda value:self._edit_boundary('end_time',value));add.clicked.connect(self._add_region);delete.clicked.connect(self._delete_region);default.clicked.connect(lambda:self.canvas.set_roi(DEFAULT_VIDEO_SUBTITLE_ROI));self._refresh_table(keep_selection=True);self._set_status()
    def _selected_roi(self):return self.regions[self.selected_index]['roi'] if self.selected_index>=0 else DEFAULT_VIDEO_SUBTITLE_ROI
    def _time_changed(self,value):self.current_time=float(value);self._set_status()
    def _preview_current(self):
        if self.preview_at is None:return
        try:self.canvas.set_preview(self.preview_at(self.current_time));self._set_status('Đã trích một khung hình gốc tại thời điểm đã chọn.')
        except Exception as exc:self._set_status(f'Không lấy được khung hình xem trước: {exc}')
    def _roi_changed(self,roi):
        if self.selected_index>=0:self.regions[self.selected_index]['roi']=dict(roi);self._refresh_table(keep_selection=True)
        self._set_status()
    def _select_row(self):
        rows=self.table.selectionModel().selectedRows()
        if not rows:return
        self.selected_index=rows[0].row();row=self.regions[self.selected_index];self.canvas.set_roi(row['roi'],emit=False);self._set_direct_spins(row);self._set_status()
    def _set_direct_spins(self,row):self.start_spin.blockSignals(True);self.end_spin.blockSignals(True);self.start_spin.setValue(row['start_time']);self.end_spin.setValue(row['end_time']);self.start_spin.blockSignals(False);self.end_spin.blockSignals(False)
    def _set_boundary(self,key):self._edit_boundary(key,self.current_time)
    def _edit_boundary(self,key,value):
        if self._syncing or self.selected_index<0:return
        self.regions[self.selected_index][key]=min(self.duration,max(0.0,float(value)));self._set_direct_spins(self.regions[self.selected_index]);self._refresh_table(keep_selection=True);self._set_status('Kiểm tra Start/End trước khi xác nhận.')
    def _add_region(self):
        if self.duration<=0:self._set_status('Video chưa có thời lượng hợp lệ.');return
        start=min(self.current_time,max(0.0,self.duration-.001));end=min(self.duration,max(start+1.0,start+.001));identifier=f'region_{len(self.regions)+1:03d}'
        while any(row['id']==identifier for row in self.regions):identifier=f'region_{len(self.regions)+2:03d}'
        self.regions.append({'id':identifier,'start_time':start,'end_time':end,'roi':dict(DEFAULT_VIDEO_SUBTITLE_ROI)});self.selected_index=len(self.regions)-1;self.canvas.set_roi(self._selected_roi(),emit=False);self._set_direct_spins(self.regions[self.selected_index]);self._refresh_table(keep_selection=True);self._set_status('Vùng mới đã thêm; đặt Start, End và chỉnh ROI trước khi xác nhận.')
    def _delete_region(self):
        if self.selected_index<0:return
        self.regions.pop(self.selected_index);self.selected_index=min(self.selected_index,len(self.regions)-1);self.canvas.set_roi(self._selected_roi(),emit=False)
        if self.selected_index>=0:self._set_direct_spins(self.regions[self.selected_index])
        self._refresh_table(keep_selection=True);self._set_status('Đã xóa vùng. Thay đổi chỉ lưu khi xác nhận.')
    def _refresh_table(self,*,keep_selection=False):
        self.table.blockSignals(True);self.table.setRowCount(len(self.regions))
        for index,row in enumerate(self.regions):
            values=(row['id'],format_region_time(row['start_time']),format_region_time(row['end_time']),format_region_time(row['end_time']-row['start_time']),'x={x_pct:.2f}, y={y_pct:.2f}, w={w_pct:.2f}, h={h_pct:.2f}'.format(**row['roi']))
            for column,value in enumerate(values):self.table.setItem(index,column,QTableWidgetItem(value))
        self.table.blockSignals(False)
        if keep_selection and self.selected_index>=0:self.table.selectRow(self.selected_index)
    def _set_status(self,extra=''):
        selected=self.regions[self.selected_index]['id'] if self.selected_index>=0 else 'chưa chọn';self.current_label.setText(f'Thời điểm xem trước: {_time_label(self.current_time)} / {_time_label(self.duration)} | Vùng chọn: {selected}');self.status.setText(extra)
    def _confirm(self):
        try:self.regions=normalize_regions(self.regions,self.duration)
        except ValueError as exc:self._set_status(str(exc));return
        self.accept()
    def value(self):return [dict(row,roi=dict(row['roi'])) for row in self.regions]
