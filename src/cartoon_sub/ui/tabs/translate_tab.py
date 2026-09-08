from PySide6.QtWidgets import (QComboBox, QPlainTextEdit, QPushButton, QLabel, QCheckBox,
    QGridLayout, QHBoxLayout, QVBoxLayout, QGroupBox, QScrollArea, QWidget, QTabWidget)
from PySide6.QtCore import Qt
from cartoon_sub.translation.presets import GENRES, STYLES


def build():
    widget = QWidget()
    outer = QVBoxLayout(widget)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    inner_tabs=QTabWidget()
    outer.addWidget(inner_tabs)
    inner_tabs.addTab(scroll,"Ngữ cảnh & văn phong")
    body = QWidget()
    layout = QVBoxLayout(body)
    scroll.setWidget(body)
    intro = QLabel("1. Chọn thể loại & văn phong → 2. Phân tích, duyệt hồ sơ truyện → 3. Dịch → kiểm tra tại Subtitle. "
                   "Gemini chỉ nhận transcript văn bản. Phân tích ngữ cảnh và dịch là các request riêng có dùng quota API.")
    intro.setWordWrap(True)
    layout.addWidget(intro)
    group = QGroupBox("Thể loại — có thể chọn kết hợp")
    grid = QGridLayout(group)
    widget.genres = {}
    for index, (key, (label, guidance)) in enumerate(GENRES.items()):
        check = QCheckBox(label)
        check.setToolTip(guidance)
        grid.addWidget(check, index // 3, index % 3)
        widget.genres[key] = check
    layout.addWidget(group)
    widget.preset = QComboBox()
    for key, (label, _) in STYLES.items():
        widget.preset.addItem(label, key)
    layout.addWidget(QLabel("Văn phong"))
    layout.addWidget(widget.preset)
    editors = QHBoxLayout()
    widget.prompt, widget.glossary = QPlainTextEdit(), QPlainTextEdit()
    for edit, label, placeholder in ((widget.prompt, "Yêu cầu biên tập bổ sung", "Ví dụ: Lời kể hiện đại; thoại sư môn cổ phong nhẹ. Không thêm cảm thán."),
                                     (widget.glossary, "Glossary bắt buộc — ưu tiên hơn đề xuất AI", "小美 -> Tiểu Mỹ\n筑基 -> Trúc Cơ")):
        box = QVBoxLayout()
        box.addWidget(QLabel(label))
        edit.setPlaceholderText(placeholder)
        edit.setMaximumHeight(120)
        box.addWidget(edit)
        editors.addLayout(box)
    layout.addLayout(editors)
    row = QHBoxLayout()
    widget.analyze_button = QPushButton("Phân tích ngữ cảnh bằng Gemini")
    widget.proposal_button = QPushButton("Duyệt đề xuất AI")
    widget.context_button = QPushButton("Hồ sơ đang áp dụng / tự nhập")
    for control in (widget.analyze_button, widget.proposal_button, widget.context_button):
        row.addWidget(control)
    layout.addLayout(row)
    widget.summary = QLabel()
    widget.summary.setTextFormat(Qt.TextFormat.PlainText)
    widget.summary.setWordWrap(True)
    widget.summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    layout.addWidget(widget.summary)
    widget.translate_button = QPushButton("Dịch / tiếp tục bản Việt bằng Gemini")
    layout.addWidget(widget.translate_button)
    note = QLabel("Hồ sơ chưa rõ có thể để trống và tự áp dụng; không bắt buộc chạy phân tích AI. "
                  "Dịch dùng cache khi dữ liệu không đổi. Đổi hồ sơ/glossary/model sẽ cần cập nhật bản dịch. "
                  "Hoàn tất tự lưu subtitle/vi.srt; cảnh báo cần biên tập không tự sửa nội dung.")
    note.setWordWrap(True)
    layout.addWidget(note)
    layout.addStretch()
    from cartoon_sub.ui.timeline_table import create_table
    timeline=QWidget(); timeline_layout=QVBoxLayout(timeline)
    toolbar=QHBoxLayout()
    widget.view=QComboBox()
    for label,key in [("Both","both"),("Subtitle","subtitle"),("Dubbing","dubbing")]: widget.view.addItem(label,key)
    widget.edit_button=QPushButton("Sửa câu chọn / mode / target")
    widget.optimize_button=QPushButton("Optimize selected for dubbing")
    for control in (widget.view,widget.edit_button,widget.optimize_button): toolbar.addWidget(control)
    timeline_layout.addLayout(toolbar)
    widget.table=create_table(); timeline_layout.addWidget(widget.table)
    hint=QLabel("Chọn nhiều dòng để tối ưu. Xanh: đúng target; vàng: lệch nhỏ; đỏ: lệch lớn/strict sai. Các mode khớp/ngắn có thể nén nghĩa; bản subtitle được giữ riêng theo Settings.")
    hint.setWordWrap(True);timeline_layout.addWidget(hint)
    inner_tabs.addTab(timeline,"Master dialogue timeline")
    return widget
