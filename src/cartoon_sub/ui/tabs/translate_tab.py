from PySide6.QtWidgets import (QComboBox, QPlainTextEdit, QPushButton, QLabel, QCheckBox,
    QGridLayout, QHBoxLayout, QVBoxLayout, QGroupBox, QScrollArea, QWidget, QTabWidget)
from PySide6.QtCore import Qt
from cartoon_sub.translation.presets import GENRES, STYLES
from cartoon_sub.ui.table_search import add_table_search


PROPER_NAME_DESCRIPTIONS = {
    "sino_vietnamese": "Tên người, địa danh, môn phái, chiêu thức và tổ chức dùng âm Hán Việt khi có cách đọc ổn định.",
    "preserve_source": "Giữ nguyên tên theo transcript/source, không tự chuyển sang âm Hán Việt.",
    "user_mapping": "Tên có mapping dùng mapping; tên chưa có mapping giữ nguyên theo nguồn.",
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
    intro = QLabel("1. Chọn thể loại & văn phong → 2. Phân tích, duyệt hồ sơ truyện → 3. Dịch → kiểm tra tại Subtitle. "
                   "Phân tích ngữ cảnh đối chiếu transcript với video theo chunk; dịch dùng visual context đã cache và không upload lại video.")
    intro.setWordWrap(True)
    layout.addWidget(intro)
    group = QGroupBox("Thể loại chính — chọn tối đa 3")
    genre_layout = QVBoxLayout(group)
    genre_help = QLabel("Chọn tối đa 3 thể loại chính để AI hiểu thế giới truyện, thuật ngữ, cách xưng hô và bối cảnh. Không chọn quá nhiều để tránh xung đột.")
    genre_help.setWordWrap(True); genre_layout.addWidget(genre_help)
    grid = QGridLayout(); genre_layout.addLayout(grid)
    widget.genres = {}
    for index, (key, (label, guidance)) in enumerate(GENRES.items()):
        check = QCheckBox(label)
        check.setToolTip(guidance)
        grid.addWidget(check, index // 3, index % 3)
        widget.genres[key] = check
    widget.genre_status = QLabel(""); widget.genre_status.setStyleSheet("color: #b45309;")
    genre_layout.addWidget(widget.genre_status)
    layout.addWidget(group)

    style_group = QGroupBox("Văn phong dịch")
    style_layout = QVBoxLayout(style_group)
    widget.preset = QComboBox()
    for key, (label, _) in STYLES.items():
        widget.preset.addItem(label, key)
    widget.style_description = QLabel(); widget.style_description.setWordWrap(True)
    style_layout.addWidget(widget.preset); style_layout.addWidget(widget.style_description)
    layout.addWidget(style_group)

    name_group = QGroupBox("Quy tắc tên riêng")
    name_layout = QVBoxLayout(name_group)
    widget.proper_name_mode = QComboBox()
    widget.proper_name_mode.addItem("Hán Việt (mặc định)", "sino_vietnamese")
    widget.proper_name_mode.addItem("Giữ nguyên theo nguồn", "preserve_source")
    widget.proper_name_mode.addItem("Theo Mapping của user", "user_mapping")
    widget.proper_name_description = QLabel(); widget.proper_name_description.setWordWrap(True)
    mapping_note = QLabel("Mapping của user luôn ưu tiên hơn quy tắc tên riêng đang chọn.")
    mapping_note.setWordWrap(True)
    name_layout.addWidget(widget.proper_name_mode); name_layout.addWidget(widget.proper_name_description); name_layout.addWidget(mapping_note)
    layout.addWidget(name_group)

    requirements_group = QGroupBox("Bối cảnh bổ sung / Yêu cầu riêng — ưu tiên cao nhất")
    requirements_layout = QVBoxLayout(requirements_group)
    requirements_help = QLabel("Ưu tiên cao nhất. Nếu để trống thì bỏ qua. Dùng để bổ sung quan hệ nhân vật, cách xưng hô, thuật ngữ bắt buộc, tên riêng, yêu cầu dịch đặc biệt hoặc thông tin AI khó suy ra từ transcript.")
    requirements_help.setWordWrap(True); requirements_layout.addWidget(requirements_help)
    editors = QHBoxLayout()
    widget.prompt, widget.glossary = QPlainTextEdit(), QPlainTextEdit()
    for edit, label, placeholder in ((widget.prompt, "Yêu cầu riêng", "Ví dụ: A và B là sư huynh đệ. Không dùng mày/tao. Nhân vật chính nói lạnh lùng nhưng không quá cổ."),
                                     (widget.glossary, "Mapping tên riêng / thuật ngữ — ưu tiên sau yêu cầu riêng", "顾沉 = Cố Trầm\n青云宗 -> Thanh Vân Tông\n灵石 = linh thạch")):
        box = QVBoxLayout()
        box.addWidget(QLabel(label))
        edit.setPlaceholderText(placeholder)
        edit.setMaximumHeight(120)
        box.addWidget(edit)
        editors.addLayout(box)
    requirements_layout.addLayout(editors)
    layout.addWidget(requirements_group)

    constraints_group = QGroupBox("Ràng buộc từ lựa chọn")
    constraints_layout = QVBoxLayout(constraints_group)
    widget.selected_contexts = QLabel("Primary genre: Chưa chọn")
    widget.selected_contexts.setWordWrap(True); constraints_layout.addWidget(widget.selected_contexts)
    widget.context_descriptions = QPlainTextEdit(); widget.context_descriptions.setReadOnly(True)
    widget.context_descriptions.setMaximumHeight(240)
    constraints_layout.addWidget(widget.context_descriptions)
    layout.addWidget(constraints_group)

    ai_group = QGroupBox("Ngữ cảnh AI")
    ai_layout = QVBoxLayout(ai_group)
    row = QHBoxLayout()
    widget.analyze_button = QPushButton("Phân tích ngữ cảnh bằng AI")
    widget.analyze_button.setToolTip("Đối chiếu transcript với video để xác định nhân vật, người nói, người được nhắc tới, quan hệ, đại từ và bối cảnh cảnh quay.")
    widget.proposal_button = QPushButton("Duyệt & lưu ngữ cảnh AI")
    for control in (widget.analyze_button, widget.proposal_button):
        row.addWidget(control)
    ai_layout.addLayout(row)
    widget.summary = QLabel()
    widget.summary.setTextFormat(Qt.TextFormat.PlainText)
    widget.summary.setWordWrap(True)
    widget.summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    ai_layout.addWidget(widget.summary)
    layout.addWidget(ai_group)
    widget.translate_button = QPushButton("Dịch / tiếp tục bản Việt bằng Gemini")
    widget.qa_button = QPushButton("QA/QC bản dịch")
    widget.qa_button.setToolTip("Chạy local checks cho toàn bộ bản dịch; chỉ gọi AI cho dòng lỗi hoặc đáng nghi.")
    widget.import_vi_button = QPushButton("Import Vietnamese SRT")
    layout.addWidget(widget.import_vi_button)
    layout.addWidget(widget.translate_button)
    layout.addWidget(widget.qa_button)
    note = QLabel("Phân tích ngữ cảnh chỉ hoàn tất khi video đã được đối chiếu; nếu video/model/API lỗi, tool báo nguyên nhân và không tạo candidate transcript-only. "
                  "Dịch dùng cache khi dữ liệu không đổi. Đổi hồ sơ/glossary/model sẽ cần cập nhật bản dịch. "
                  "Hoàn tất tự lưu subtitle/vi.srt; cảnh báo cần biên tập không tự sửa nội dung.")
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
    widget.prompt.textChanged.connect(update_context_description)
    widget.glossary.textChanged.connect(update_context_description)
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
    widget.search_edit, widget.search_clear = add_table_search(timeline_layout, widget.table, (0,4,5,7,8))
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
