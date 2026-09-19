from PySide6.QtWidgets import QPushButton, QTextEdit
from .common import page

def build():
    widget, layout = page("Tạo project từ video MP4/MKV/MOV. Video gốc được tham chiếu tại vị trí hiện tại.")
    widget.open_button = QPushButton("Mở video / Tạo project")
    widget.metadata = QTextEdit()
    widget.metadata.setReadOnly(True)
    layout.addWidget(widget.open_button)
    layout.addWidget(widget.metadata)
    return widget
