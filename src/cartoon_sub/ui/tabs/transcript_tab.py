from PySide6.QtWidgets import (QPushButton, QGroupBox, QVBoxLayout, QHBoxLayout,
    QLabel, QComboBox, QLineEdit, QTableWidgetItem)
from .common import page, table
from cartoon_sub.subtitle.timestamps import format_srt_timestamp
from cartoon_sub.ui.table_search import add_table_search, apply_table_search
from cartoon_sub.ui.cache_status_widget import CacheStatusBox


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
    widget, layout = page("Import SRT tiếng Trung không gọi AI. Hoặc chọn Model STT rồi chạy Chinese Transcript. "
                          "Model STT là provider/model transcription chỉ nhận dạng audio; Model AI cho tab dùng cho phân tích ngữ nghĩa/context và tự lưu zh.srt. "
                          "Chạy lại dùng cache và thay danh sách subtitle. "
                          "Project đã import SRT sẽ tắt transcription. Editor sẽ triển khai ở phase sau.")
    widget.import_button = QPushButton("Import Chinese SRT")
    stt_row = QHBoxLayout(); widget.stt_model_search = QLineEdit(); widget.stt_model = QComboBox()
    widget.stt_model_search.setPlaceholderText("Tìm model STT theo provider, tên hoặc ID…")
    widget.stt_model_effective = QLabel()
    stt_row.addWidget(QLabel("Model STT")); stt_row.addWidget(widget.stt_model_search, 1)
    stt_row.addWidget(widget.stt_model, 2); stt_row.addWidget(widget.stt_model_effective)
    layout.addLayout(stt_row)
    model_row = QHBoxLayout(); widget.ai_model_search = QLineEdit(); widget.ai_model = QComboBox(); widget.ai_model_effective = QLabel()
    widget.ai_model_search.setPlaceholderText("Tìm model AI theo provider, tên hoặc ID…")
    model_row.addWidget(QLabel("Model AI cho phân tích ngữ nghĩa")); model_row.addWidget(widget.ai_model_search, 1); model_row.addWidget(widget.ai_model, 2)
    widget.reset_ai_button = QPushButton("Xóa dữ liệu AI / Chạy lại")
    model_row.addWidget(widget.ai_model_effective); model_row.addWidget(widget.reset_ai_button); layout.addLayout(model_row)
    widget.cache_status = CacheStatusBox(); layout.addWidget(widget.cache_status)
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
