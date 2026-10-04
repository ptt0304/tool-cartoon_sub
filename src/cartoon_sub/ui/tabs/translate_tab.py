from PySide6.QtWidgets import (QComboBox, QLineEdit, QPlainTextEdit, QPushButton, QLabel, QCheckBox,
    QGridLayout, QHBoxLayout, QVBoxLayout, QGroupBox, QScrollArea, QWidget, QTabWidget)
from PySide6.QtCore import Qt
from cartoon_sub.translation.presets import GENRES, STYLES
from cartoon_sub.ui.no_wheel import NoWheelComboBox
from cartoon_sub.ui.table_search import add_table_search
from cartoon_sub.ui.cache_status_widget import CacheStatusBox


PROPER_NAME_DESCRIPTIONS = {
    "sino_vietnamese": "Tên người, địa danh, môn phái, chiêu thức và tổ chức dùng âm Hán Việt khi có cách đọc ổn định.",
    "preserve_source": "Giữ nguyên tên theo transcript/source, không tự chuyển sang âm Hán Việt.",
    "custom": "Dùng quy tắc tên riêng do người dùng nhập. Mapping đã lưu vẫn được ưu tiên.",
}


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
    model_row = QHBoxLayout(); widget.ai_model_search = QLineEdit(); widget.ai_model = NoWheelComboBox(); widget.ai_model_effective = QLabel()
    widget.ai_model_search.setPlaceholderText("Tìm model theo provider, tên hoặc ID…")
    model_row.addWidget(QLabel("Model AI cho tab")); model_row.addWidget(widget.ai_model_search, 1); model_row.addWidget(widget.ai_model, 2)
    widget.reset_ai_button = QPushButton("Xóa dữ liệu AI / Chạy lại")
    model_row.addWidget(widget.ai_model_effective); model_row.addWidget(widget.reset_ai_button); layout.addLayout(model_row)
    widget.cache_status = CacheStatusBox(); layout.addWidget(widget.cache_status)
    intro = QLabel("Chọn hướng dẫn dịch rồi bấm Dịch. AI dịch theo từng đoạn hội thoại, dùng video/audio "
                   "quanh timestamp và tự chạy QA/QC tiếng Việt; không cần phân tích hay duyệt ngữ cảnh trước.")
    intro.setWordWrap(True)
    layout.addWidget(intro)
    group = QGroupBox("Thể loại chính — chọn tối đa 3")
    genre_layout = QVBoxLayout(group)
    genre_help = QLabel("Chọn tối đa 3 thể loại chính để AI hiểu thế giới truyện, thuật ngữ, cách xưng hô và bối cảnh. Không chọn quá nhiều để tránh xung đột.")
    genre_help.setWordWrap(True); genre_layout.addWidget(genre_help)
    grid = QGridLayout(); genre_layout.addLayout(grid)
    widget.genres = {}
    genre_items = [*GENRES.items(), ("custom", ("Tùy chỉnh", "Dùng Thể loại chính do người dùng nhập."))]
    for index, (key, (label, guidance)) in enumerate(genre_items):
        check = QCheckBox(label)
        check.setToolTip(guidance)
        grid.addWidget(check, index % 9, index // 9)
        widget.genres[key] = check
    for column in range(4):
        grid.setColumnStretch(column, 1)
    widget.genre_status = QLabel(""); widget.genre_status.setStyleSheet("color: #b45309;")
    genre_layout.addWidget(widget.genre_status)
    layout.addWidget(group)

    style_group = QGroupBox("Văn phong dịch")
    style_layout = QVBoxLayout(style_group)
    widget.preset = NoWheelComboBox()
    for key, (label, _) in STYLES.items():
        widget.preset.addItem(label, key)
    widget.style_description = QLabel(); widget.style_description.setWordWrap(True)
    style_layout.addWidget(widget.preset); style_layout.addWidget(widget.style_description)
    layout.addWidget(style_group)

    name_group = QGroupBox("Quy tắc tên riêng")
    name_layout = QVBoxLayout(name_group)
    widget.proper_name_mode = NoWheelComboBox()
    widget.proper_name_mode.addItem("Hán Việt (mặc định)", "sino_vietnamese")
    widget.proper_name_mode.addItem("Giữ nguyên theo nguồn", "preserve_source")
    widget.proper_name_mode.addItem("Tùy chỉnh", "custom")
    widget.proper_name_description = QLabel(); widget.proper_name_description.setWordWrap(True)
    mapping_note = QLabel("Mapping của user luôn ưu tiên hơn quy tắc tên riêng đang chọn.")
    mapping_note.setWordWrap(True)
    name_layout.addWidget(widget.proper_name_mode); name_layout.addWidget(widget.proper_name_description); name_layout.addWidget(mapping_note)
    layout.addWidget(name_group)

    requirements_group = QGroupBox("Người dùng tự định nghĩa")
    requirements_layout = QVBoxLayout(requirements_group)
    requirements_help = QLabel("Ba quy tắc độc lập. Chỉ quy tắc có selector Tùy chỉnh mới được gửi cho AI. Có thể để trống.")
    requirements_help.setWordWrap(True); requirements_layout.addWidget(requirements_help)
    editors = QHBoxLayout()
    # Hidden legacy stores keep old projects and saved mappings intact while
    # the visible interface has exactly the three requested custom controls.
    widget.prompt, widget.glossary = QPlainTextEdit(), QPlainTextEdit()
    widget.custom_genre, widget.custom_style, widget.custom_name_rule = (
        QPlainTextEdit(), QPlainTextEdit(), QPlainTextEdit())
    for edit, label, placeholder in (
        (widget.custom_genre, "Thể loại chính", "Ví dụ: Tiên hiệp hài, hệ thống nhiệm vụ và nhịp thoại nhanh."),
        (widget.custom_style, "Văn phong chính", "Ví dụ: Câu Việt ngắn, tự nhiên, giữ chất châm biếm nhẹ."),
        (widget.custom_name_rule, "Quy tắc tên riêng", "Ví dụ: Giữ tên theo pinyin; danh xưng dịch Hán Việt."),
    ):
        box = QVBoxLayout()
        box.addWidget(QLabel(label))
        edit.setPlaceholderText(placeholder)
        edit.setMaximumHeight(120)
        box.addWidget(edit)
        editors.addLayout(box, 1)
    requirements_layout.addLayout(editors)
    layout.addWidget(requirements_group)

    widget.selected_contexts = QLabel("Primary genre: Chưa chọn")
    widget.selected_contexts.setWordWrap(True)
    widget.context_descriptions = QPlainTextEdit(); widget.context_descriptions.setReadOnly(True)
    widget.context_descriptions.setMaximumHeight(240)
    widget.selected_contexts.hide(); widget.context_descriptions.hide()

    # Legacy actions stay as hidden compatibility attributes for old projects
    # and controller wiring; they are no longer part of the normal workflow.
    widget.analyze_button = QPushButton("Phân tích Speaker & Ngữ cảnh AI")
    widget.proposal_button = QPushButton("Duyệt & lưu ngữ cảnh AI")
    widget.analyze_button.hide(); widget.proposal_button.hide()
    widget.summary = QLabel()
    widget.summary.setTextFormat(Qt.TextFormat.PlainText)
    widget.summary.setWordWrap(True)
    widget.summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    widget.translate_button = QPushButton("Dịch")
    widget.qa_button = QPushButton("QA/QC bản dịch")
    widget.qa_button.setToolTip("Chạy local checks cho toàn bộ bản dịch; chỉ gọi AI cho dòng lỗi hoặc đáng nghi.")
    widget.import_vi_button = QPushButton("Import Vietnamese SRT")
    layout.addWidget(widget.import_vi_button)
    layout.addWidget(widget.translate_button)
    layout.addWidget(widget.qa_button)
    layout.addWidget(widget.summary)
    note = QLabel("Dịch dùng cache khi dữ liệu không đổi. Đổi thể loại, văn phong, quy tắc tên, bối cảnh người dùng "
                  "hoặc model sẽ làm Translation/QA cần chạy lại nhưng không ảnh hưởng STT hay Master Timeline. "
                  "Hoàn tất sẽ tự lưu subtitle/vi.srt.")
    note.setWordWrap(True)
    layout.addWidget(note)
    layout.addStretch()

    widget.genre_guard = False
    def update_context_description(*_):
        selected = [(key, GENRES[key]) for key, check in widget.genres.items() if check.isChecked()]
        primary = selected[0][1][0] if selected else "Chưa chọn"
        secondary = ", ".join(value[0] for _, value in selected[1:]) or "Không có"
        widget.selected_contexts.setText(f"Primary genre: {primary}\nSecondary genres: {secondary}")
        style_key = widget.preset.currentData() or "Natural Vietnamese"
        style_label, style_description = STYLES.get(style_key, STYLES["Natural Vietnamese"])
        widget.style_description.setText(style_description)
        proper_key = widget.proper_name_mode.currentData() or "sino_vietnamese"
        proper_description = PROPER_NAME_DESCRIPTIONS[proper_key]
        widget.proper_name_description.setText(proper_description)
        mappings = widget.glossary.toPlainText().strip()
        supplemental = widget.prompt.toPlainText().strip()
        genre_details = "\n".join(f"- {value[0]}: {value[1]}" for _, value in selected) or "- Chưa chọn"
        sections = [
            "Primary genre:\n" + primary,
            "Secondary genres:\n" + secondary,
            "Translation style:\n" + style_label + " — " + style_description,
            "Proper-name rule:\n" + widget.proper_name_mode.currentText() + " — " + proper_description,
            "User mappings:\n" + (mappings or "Không có"),
            "Supplemental requirements:\n" + (supplemental or "Không có"),
            "Genre guidance:\n" + genre_details,
        ]
        widget.context_descriptions.setPlainText("\n\n".join(sections))

    def on_genre_toggled(key, checked):
        if widget.genre_guard:
            return
        selected = [name for name, check in widget.genres.items() if check.isChecked()]
        if checked and len(selected) > 3:
            widget.genre_guard = True
            widget.genres[key].setChecked(False)
            widget.genre_guard = False
            widget.genre_status.setText("Chỉ nên chọn tối đa 3 thể loại chính.")
        else:
            widget.genre_status.clear()
        update_context_description()

    widget.update_context_description = update_context_description
    for key, check in widget.genres.items():
        check.toggled.connect(lambda checked, genre_key=key: on_genre_toggled(genre_key, checked))
    widget.preset.currentIndexChanged.connect(update_context_description)
    widget.proper_name_mode.currentIndexChanged.connect(update_context_description)
    for edit in (widget.prompt, widget.glossary, widget.custom_genre,
                 widget.custom_style, widget.custom_name_rule):
        edit.textChanged.connect(update_context_description)
    from cartoon_sub.ui.timeline_table import create_table, install_delta_target_filter
    timeline=QWidget(); timeline_layout=QVBoxLayout(timeline)
    toolbar=QHBoxLayout()
    widget.view=QComboBox()
    for label,key in [("Both","both"),("Subtitle","subtitle"),("Dubbing","dubbing")]: widget.view.addItem(label,key)
    widget.edit_button=QPushButton("Sửa câu chọn / mode / target")
    widget.optimize_button=QPushButton("Tối ưu dubbing đã chọn")
    widget.optimize_bulk_button=QPushButton("Tối ưu dubbing hàng loạt")
    widget.manual_qa_button=QPushButton("QA/QC AI dòng đã chọn")
    widget.manual_qa_button.setToolTip("Luôn gọi AI để kiểm tra riêng các dòng đang chọn; dòng PASS giữ nguyên.")
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
    for control in (widget.view,widget.edit_button,widget.optimize_button,widget.optimize_bulk_button,widget.manual_qa_button,widget.revert_optimize_button,widget.apply_edits_button,widget.revert_edits_button,widget.edits_status_label): toolbar.addWidget(control)
    toolbar.addStretch()
    timeline_layout.addLayout(toolbar)
    widget.table=create_table()
    widget.search_edit, widget.search_clear = add_table_search(timeline_layout, widget.table, (0,4,5,7,8))
    widget.delta_min, widget.delta_max = install_delta_target_filter(widget.table)
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
    export_group=QGroupBox("EXPORT TRANSLATE")
    export_layout=QVBoxLayout(export_group)
    widget.export_translate_button=QPushButton("Export Translate SRT")
    widget.export_translate_status=QLabel();widget.export_translate_status.setWordWrap(True)
    export_layout.addWidget(widget.export_translate_button);export_layout.addWidget(widget.export_translate_status)
    speaker_row=QHBoxLayout()
    widget.speaker_text_type=QComboBox()
    widget.speaker_text_type.addItem("Vietnamese Dubbing", "vi_dubbing")
    widget.speaker_text_type.addItem("Vietnamese Subtitle", "vi_subtitle")
    widget.export_speakers_button=QPushButton("Export SRT + TXT/manifest theo speaker")
    speaker_row.addWidget(widget.speaker_text_type);speaker_row.addWidget(widget.export_speakers_button)
    export_layout.addLayout(speaker_row)
    widget.export_speakers_status=QLabel();widget.export_speakers_status.setWordWrap(True)
    export_layout.addWidget(widget.export_speakers_status)
    timeline_layout.addWidget(export_group)
    inner_tabs.addTab(timeline,"Master dialogue timeline")
    update_context_description()
    return widget
