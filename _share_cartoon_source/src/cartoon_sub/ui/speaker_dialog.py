from pathlib import Path
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QComboBox,QPushButton,QLabel,QInputDialog,QMessageBox,QTableWidget,QTableWidgetItem,QAbstractItemView,QHeaderView)
from PySide6.QtCore import Qt,QUrl,QTimer
from PySide6.QtMultimedia import QMediaPlayer,QAudioOutput
from cartoon_sub.subtitle.models import Project
from cartoon_sub.speaker import editor_service as service
from cartoon_sub.speaker.service import refresh_timeline,approve_review,speaker_counts

class SpeakerDialog(QDialog):
    def __init__(self,project,directory,parent=None):
        super().__init__(parent)
        self.project=Project.from_dict(project.to_dict()); self.directory=Path(directory)
        self.setWindowTitle("Speaker review — mỗi dòng là một utterance độc lập")
        self.resize(1150,720)
        box=QVBoxLayout(self)
        note=QLabel("Speaker là giọng nói, không tự đồng nhất với nhân vật. Chọn nhiều dòng để gán/tách speaker. SPK_UNKNOWN phải được gán trước khi xác nhận. Hai dòng chồng nhau được giữ nguyên timestamp."); note.setWordWrap(True); box.addWidget(note)
        row=QHBoxLayout(); self.filter=QComboBox(); self.target=QComboBox(); row.addWidget(QLabel("Lọc"));row.addWidget(self.filter);row.addWidget(QLabel("Speaker thao tác"));row.addWidget(self.target);box.addLayout(row)
        self.table=QTableWidget(0,8);self.table.setHorizontalHeaderLabels(["ID","Start","End","Duration","Speaker","Overlap","Chinese","AI confidence"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows);self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection);self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers);self.table.setColumnWidth(6,350)
        self.table.setWordWrap(True);self.table.setTextElideMode(Qt.TextElideMode.ElideNone);self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn);self.table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive);self.table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive);self.table.horizontalHeader().sectionResized.connect(lambda *_:self.table.resizeRowsToContents());box.addWidget(self.table)
        row=QHBoxLayout()
        for label,action in [("Chọn tất cả",self.table.selectAll),("Speaker mới",self.add),("Đổi tên",self.rename),("Gán dòng chọn",self.assign),("Tách dòng chọn",self.split),("Gộp vào…",self.merge),("Nghe dòng chọn",self.play)]:
            button=QPushButton(label);button.clicked.connect(action);row.addWidget(button)
        box.addLayout(row)
        row=QHBoxLayout()
        save=QPushButton("Lưu chỉnh sửa, chưa xác nhận");approve=QPushButton("Xác nhận speaker và lưu");cancel=QPushButton("Hủy")
        save.clicked.connect(self.accept);approve.clicked.connect(self.approve);cancel.clicked.connect(self.reject)
        for b in (save,approve,cancel):row.addWidget(b)
        box.addLayout(row)
        self.player=QMediaPlayer(self);self.audio=QAudioOutput(self);self.player.setAudioOutput(self.audio)
        self.player.errorOccurred.connect(lambda *args: QMessageBox.warning(self,"Audio",self.player.errorString()))
        self.end_ms=0;self.start_ms=0
        self.play_pending=False
        self.player.mediaStatusChanged.connect(self.loaded)
        self.timer=QTimer(self);self.timer.setInterval(50);self.timer.timeout.connect(self.check_playback)
        self.filter.currentIndexChanged.connect(self.fill)
        self.refresh()
    def ids(self):
        return [int(self.table.item(i.row(),0).text()) for i in self.table.selectionModel().selectedRows()]
    def refresh(self):
        refresh_timeline(self.project); counts=speaker_counts(self.project)
        current=self.filter.currentData(); target=self.target.currentData()
        self.filter.blockSignals(True);self.filter.clear();self.filter.addItem("Tất cả",None);self.target.clear()
        for sid,info in self.project.speakers.items():
            label=f"{sid} · {info['name']} ({counts.get(sid,0)} câu)";self.filter.addItem(label,sid);self.target.addItem(label,sid)
        self.filter.setCurrentIndex(max(0,self.filter.findData(current)));self.target.setCurrentIndex(max(0,self.target.findData(target)));self.filter.blockSignals(False);self.fill()
    def fill(self):
        rows=[s for s in self.project.segments if not self.filter.currentData() or s.speaker_id==self.filter.currentData()]
        self.table.setRowCount(len(rows))
        for r,s in enumerate(rows):
            for c,value in enumerate([s.id,f"{s.start:.3f}",f"{s.end:.3f}",f"{s.duration:.3f}",s.speaker_id,s.overlap_group or "No",s.zh,s.speaker_confidence if s.speaker_confidence is not None else "Unknown"]):self.table.setItem(r,c,QTableWidgetItem(str(value)))
        self.table.resizeRowsToContents()
    def run_edit(self,fn,*args):
        try:fn(self.project,*args);self.refresh()
        except ValueError as e:QMessageBox.warning(self,"Speaker",str(e))
    def add(self):self.run_edit(service.new_speaker)
    def rename(self):
        sid=self.target.currentData()
        if not sid:return
        name,ok=QInputDialog.getText(self,"Tên speaker","Tên hiển thị",text=self.project.speakers[sid]['name'])
        if ok:self.run_edit(service.rename,sid,name)
    def assign(self):self.run_edit(service.assign,self.ids(),self.target.currentData())
    def split(self):self.run_edit(service.split,self.ids())
    def merge(self):
        source=self.target.currentData(); candidates=[s for s in self.project.speakers if s!=source]
        if not candidates:return
        target,ok=QInputDialog.getItem(self,"Gộp speaker",f"Gộp {source} vào",candidates,editable=False)
        if ok:self.run_edit(service.merge,source,target)
    def approve(self):
        self.stop_playback()
        try:approve_review(self.project);self.accept()
        except ValueError as e:QMessageBox.warning(self,"Speaker",str(e))
    def play(self):
        self.stop_playback()
        ids=self.ids()
        if not ids:return
        s=next(s for s in self.project.segments if s.id==ids[0])
        source=self.directory/'audio'/'source.wav'
        if not source.exists():source=Path(self.project.source_video_path)
        if not source.exists():QMessageBox.warning(self,"Audio","Không tìm thấy audio/video nguồn");return
        self.start_ms=round(s.start*1000);self.end_ms=round(s.end*1000)
        self.play_pending=True
        url=QUrl.fromLocalFile(str(source.resolve()))
        if self.player.source()!=url:self.player.setSource(url)
        elif self.player.mediaStatus() in (QMediaPlayer.MediaStatus.LoadedMedia,QMediaPlayer.MediaStatus.BufferedMedia,QMediaPlayer.MediaStatus.EndOfMedia):
            self.loaded(QMediaPlayer.MediaStatus.LoadedMedia)
    def loaded(self,status):
        if status in (QMediaPlayer.MediaStatus.LoadedMedia,QMediaPlayer.MediaStatus.BufferedMedia) and self.play_pending and self.end_ms:
            # Consume the explicit request before play() emits further media events.
            self.play_pending=False
            self.player.setPosition(self.start_ms);self.player.play();self.timer.start()
    def check_playback(self):
        if self.end_ms and self.player.position()>=self.end_ms:self.stop_playback()
    def stop_playback(self):
        # stop() can emit LoadedMedia synchronously or later. Invalidate first.
        self.play_pending=False
        self.end_ms=0;self.start_ms=0
        self.timer.stop()
        self.player.stop()
    def done(self,result):
        self.stop_playback();super().done(result)
    def hideEvent(self,event):
        self.stop_playback();super().hideEvent(event)
