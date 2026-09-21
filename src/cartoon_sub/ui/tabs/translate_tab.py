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
    group = QGroupBox("Ngữ cảnh / văn phong dịch — có thể chọn nhiều")
    grid = QGridLayout(group)
    widget.genres = {}
    for index, (key, (label, guidance)) in enumerate(GENRES.items()):
        check = QCheckBox(label)
        check.setToolTip(guidance)
        grid.addWidget(check, index // 3, index % 3)
        widget.genres[key] = check
    layout.addWidget(group)
    widget.selected_contexts = QLabel("Ngữ cảnh đang chọn: Chưa chọn")
    widget.selected_contexts.setWordWrap(True); layout.addWidget(widget.selected_contexts)
    widget.context_descriptions = QPlainTextEdit(); widget.context_descriptions.setReadOnly(True)
    widget.context_descriptions.setMaximumHeight(180)
    widget.context_descriptions.setPlaceholderText("Chọn context để xem mô tả chi tiết.")
    layout.addWidget(QLabel("Mô tả context")); layout.addWidget(widget.context_descriptions)
    def update_context_description(*_):
        selected=[(key, GENRES[key]) for key,check in widget.genres.items() if check.isChecked()]
        widget.selected_contexts.setText("Ngữ cảnh đang chọn: " + (" + ".join(value[0] for _,value in selected) or "Chưa chọn"))
        widget.context_descriptions.setPlainText("\n\n".join(f"{value[0]}\n{'─'*len(value[0])}\n{value[1]}" for _,value in selected))
    widget.update_context_description = update_context_description
    for check in widget.genres.values(): check.toggled.connect(update_context_description)
    widget.preset = QComboBox()
    for key, (label, _) in STYLES.items():
        widget.preset.addItem(label, key)
    layout.addWidget(QLabel("Văn phong"))
    layout.addWidget(widget.preset)
    widget.proper_name_mode = QComboBox()
    widget.proper_name_mode.addItem("Hán Việt (mặc định)", "sino_vietnamese")
    widget.proper_name_mode.addItem("Giữ nguyên theo nguồn", "preserve_source")
    widget.proper_name_mode.addItem("Theo Mapping của user", "user_mapping")
    layout.addWidget(QLabel("Tên riêng Trung Quốc")); layout.addWidget(widget.proper_name_mode)
    editors = QHBoxLayout()
    widget.prompt, widget.glossary = QPlainTextEdit(), QPlainTextEdit()
    for edit, label, placeholder in ((widget.prompt, "Bối cảnh bổ sung / yêu cầu riêng", "Ví dụ: SPK_01 là hoàng đế. SPK_03 luôn xưng thần với SPK_01."),
                                     (widget.glossary, "Từ điển / Mapping — ưu tiên cao nhất", "Xuanyi = Huyền Nhất\nQingyun Sect -> Thanh Vân Tông\n# có thể ghi chú")):
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
    widget.apply_context_button = QPushButton("Áp dụng ngữ cảnh / văn phong")
    layout.addWidget(widget.apply_context_button)
    widget.import_vi_button = QPushButton("Import Vietnamese SRT")
    layout.addWidget(widget.import_vi_button)
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
    widget.revert_optimize_button=QPushButton("Revert selected dubbing optimization")
    widget.revert_optimize_button.setToolTip("Khôi phục VI Subtitle và VI Dubbing về trước lần Optimize for dubbing đầu tiên.")
    widget.apply_edits_button=QPushButton("Áp dụng bản sửa tay")
    widget.apply_edits_button.setToolTip("Lưu các thay đổi thủ công vào master timeline và cập nhật các bước Subtitle/Audio liên quan.")
    widget.apply_edits_button.setEnabled(False)
    widget.revert_edits_button=QPushButton("Hoàn tác sửa tay")
    widget.revert_edits_button.setToolTip("Khôi phục lại dữ liệu chưa áp dụng từ project.")
    widget.revert_edits_button.setEnabled(False)
    widget.edits_status_label=QLabel("")
    widget.edits_status_label.setStyleSheet("color: #666; font-size: 11px;")
    for control in (widget.view,widget.edit_button,widget.optimize_button,widget.revert_optimize_button,widget.apply_edits_button,widget.revert_edits_button,widget.edits_status_label): toolbar.addWidget(control)
    toolbar.addStretch()
    timeline_layout.addLayout(toolbar)
    widget.table=create_table()
    def on_dirty_changed(count):
        widget.apply_edits_button.setEnabled(count > 0)
        widget.revert_edits_button.setEnabled(count > 0)
        if count > 0:
            widget.edits_status_label.setText(f"Có {count} dòng chưa áp dụng")
            widget.edits_status_label.setStyleSheet("color: #d97706; font-weight: bold; font-size: 11px;")
        else:
            widget.edits_status_label.setText("")
    widget.table.on_dirty_changed = on_dirty_changed
    timeline_layout.addWidget(widget.table)
    hint=QLabel("Nhấp đúp vào ô để sửa trực tiếp (Start, End, Speaker, Chinese, VI Subtitle, VI Dubbing), sau đó bấm 'Áp dụng bản sửa tay'.")
    hint.setWordWrap(True);timeline_layout.addWidget(hint)
    inner_tabs.addTab(timeline,"Master dialogue timeline")
    update_context_description()
    return widget
