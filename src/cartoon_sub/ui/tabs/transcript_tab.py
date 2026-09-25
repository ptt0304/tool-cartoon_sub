from PySide6.QtWidgets import QPushButton, QGroupBox, QVBoxLayout, QLabel
from .common import page, table

def build():
    widget, layout = page("Import SRT tiếng Trung không gọi AI. Hoặc cấu hình Settings > AI rồi chạy Chinese Transcript. "
                          "Tool sử dụng provider/model transcription đã chọn trong Settings > AI và tự lưu zh.srt. "
                          "Chạy lại dùng cache và thay danh sách subtitle. "
                          "Project đã import SRT sẽ tắt transcription. Editor sẽ triển khai ở phase sau.")
    widget.import_button = QPushButton("Import Chinese SRT")
    widget.transcribe_button = QPushButton("Run Chinese Transcript")
    widget.speaker_button = QPushButton("Speaker review / gán, đổi tên, gộp, tách và nghe")
    widget.table = table(["ID", "Start (s)", "End (s)", "Duration", "Speaker", "Overlap", "Chinese"])
    for control in (widget.import_button, widget.transcribe_button, widget.speaker_button, widget.table):
        layout.addWidget(control)
    export_group = QGroupBox("EXPORT TRANSCRIPT")
    export_layout = QVBoxLayout(export_group)
    widget.export_transcript_button = QPushButton("Export Transcript SRT")
    widget.export_transcript_status = QLabel()
    widget.export_transcript_status.setWordWrap(True)
    export_layout.addWidget(widget.export_transcript_button)
    export_layout.addWidget(widget.export_transcript_status)
    layout.addWidget(export_group)
    return widget
