from PySide6.QtWidgets import QPushButton
from .common import page, table

def build():
    widget, layout = page("Import SRT tiếng Trung không gọi AI. Hoặc cấu hình Settings > AI rồi chạy Gemini transcription. "
                          "Tool chỉ gửi audio lên Google, tự lưu zh.srt. Chạy lại dùng cache và thay danh sách subtitle. "
                          "Project đã import SRT sẽ tắt transcription. Editor sẽ triển khai ở phase sau.")
    widget.import_button = QPushButton("Import Chinese SRT")
    widget.transcribe_button = QPushButton("Gemini: tạo / tiếp tục Chinese transcript")
    widget.speaker_button = QPushButton("Speaker review / gán, đổi tên, gộp, tách và nghe")
    widget.table = table(["ID", "Start (s)", "End (s)", "Duration", "Speaker", "Overlap", "Chinese"])
    for control in (widget.import_button, widget.transcribe_button, widget.speaker_button, widget.table):
        layout.addWidget(control)
    return widget
