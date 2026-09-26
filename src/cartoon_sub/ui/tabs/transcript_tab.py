from PySide6.QtWidgets import QPushButton, QGroupBox, QVBoxLayout, QLabel, QTableWidgetItem
from .common import page, table
from cartoon_sub.subtitle.timestamps import format_srt_timestamp
from cartoon_sub.ui.table_search import add_table_search, apply_table_search


def populate(widget, project):
    target = widget.table
    target.setRowCount(len(project.segments))
    for row, segment in enumerate(project.segments):
        overlap_status = segment.overlap_group or (segment.overlap_type if segment.overlap_type != "NONE" else "No")
        values = [segment.id, format_srt_timestamp(segment.start), format_srt_timestamp(segment.end),
                  f"{segment.duration:.3f}", f"{segment.speaker_id} · {segment.speaker_name}",
                  overlap_status, segment.zh]
        for column, value in enumerate(values):
            item = QTableWidgetItem(str(value))
            item.setToolTip(str(value))
            target.setItem(row, column, item)
    apply_table_search(target)
    target.resizeRowsToContents()

def build():
    widget, layout = page("Import SRT tiếng Trung không gọi AI. Hoặc cấu hình Settings > AI rồi chạy Chinese Transcript. "
                          "Tool sử dụng provider/model transcription đã chọn trong Settings > AI và tự lưu zh.srt. "
                          "Chạy lại dùng cache và thay danh sách subtitle. "
                          "Project đã import SRT sẽ tắt transcription. Editor sẽ triển khai ở phase sau.")
    widget.import_button = QPushButton("Import Chinese SRT")
    widget.transcribe_button = QPushButton("Run Chinese Transcript")
    widget.speaker_button = QPushButton("Speaker review / gán, đổi tên, gộp, tách và nghe")
    widget.table = table(["ID", "Start", "End", "Duration", "Speaker", "Overlap", "Chinese"])
    for control in (widget.import_button, widget.transcribe_button, widget.speaker_button):
        layout.addWidget(control)
    widget.search_edit, widget.search_clear = add_table_search(layout, widget.table, (0, 4, 6))
    layout.addWidget(widget.table)
    export_group = QGroupBox("EXPORT TRANSCRIPT")
    export_layout = QVBoxLayout(export_group)
    widget.export_transcript_button = QPushButton("Export Transcript SRT")
    widget.export_transcript_status = QLabel()
    widget.export_transcript_status.setWordWrap(True)
    export_layout.addWidget(widget.export_transcript_button)
    export_layout.addWidget(widget.export_transcript_status)
    layout.addWidget(export_group)
    return widget
