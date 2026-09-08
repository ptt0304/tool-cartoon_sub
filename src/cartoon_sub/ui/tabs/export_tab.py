from PySide6.QtWidgets import QComboBox,QPushButton,QLabel
from .common import page

def build():
    widget,layout=page("Export từ master timeline. Timestamp giữa speaker có thể chồng nhau và được giữ nguyên. Mỗi lần export tạo một snapshot mới để không lẫn file cũ.")
    widget.text_type=QComboBox(); widget.text_type.addItem("Vietnamese Dubbing","vi_dubbing");widget.text_type.addItem("Vietnamese Subtitle","vi_subtitle")
    widget.export_button=QPushButton("Export SRT + TXT/manifest theo speaker")
    widget.path_label=QLabel(); widget.path_label.setWordWrap(True)
    layout.addWidget(widget.text_type);layout.addWidget(widget.export_button);layout.addWidget(widget.path_label)
    layout.addStretch()
    return widget
