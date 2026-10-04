from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QFormLayout,QLabel,QPushButton,QComboBox,QLineEdit,
    QDoubleSpinBox,QSpinBox,QTreeWidget,QTreeWidgetItem,QAbstractItemView,QHeaderView,QGroupBox)

from cartoon_sub.subtitle.segmentation import SegmentationProfile, SegmentationSettings, settings_for
from cartoon_sub.subtitle.segmentation_service import SubtitleSegmentationService, subtitle_source_text, subtitle_source_warning
from cartoon_sub.subtitle.timestamps import format_srt_timestamp
from cartoon_sub.ui.table_search import add_tree_search, apply_tree_search
from cartoon_sub.ui.cache_status_widget import CacheStatusBox


PROFILE_LABELS = {
    SegmentationProfile.BALANCED: "Cân bằng", SegmentationProfile.READING_COMFORT: "Ưu tiên dễ đọc",
    SegmentationProfile.FAST_DIALOGUE: "Hội thoại nhanh", SegmentationProfile.PRESERVE_SENTENCES: "Giữ nguyên câu",
    SegmentationProfile.CUSTOM: "Tùy chỉnh",
}
QC_FILTERS = ("OK", "TOO_LONG", "TOO_SHORT", "TOO_MANY_SYLLABLES", "TOO_MANY_LINES",
              "TOO_MANY_CHARS_PER_LINE", "HIGH_READING_SPEED", "BAD_SPLIT", "MANUAL_REVIEW")


class SubtitlePage(QWidget):
    def __init__(self):
        super().__init__(); self.loading = False
        layout = QVBoxLayout(self)
        note = QLabel("Utterance là lời thoại nguồn; DisplaySegment là cách hiển thị. Auto Segment không gọi Gemini, "
                      "không sửa transcript/dịch và giữ timestamp overlap giữa speaker. Chọn câu dài rồi bấm "
                      "‘Căn timing audio’ để Gemini nghe audio và đặt mốc hiển thị chi tiết.")
        note.setWordWrap(True); layout.addWidget(note)
        model_row = QHBoxLayout(); self.ai_model_search = QLineEdit(); self.ai_model = QComboBox(); self.ai_model_effective = QLabel()
        self.ai_model_search.setPlaceholderText("Tìm model theo provider, tên hoặc ID…")
        model_row.addWidget(QLabel("Model AI cho tab")); model_row.addWidget(self.ai_model_search, 1); model_row.addWidget(self.ai_model, 2)
        self.reset_ai_button = QPushButton("Xóa dữ liệu AI / Chạy lại")
        model_row.addWidget(self.ai_model_effective); model_row.addWidget(self.reset_ai_button); layout.addLayout(model_row)
        self.cache_status = CacheStatusBox(); layout.addWidget(self.cache_status)
        source_row = QHBoxLayout();self.text_source = QComboBox()
        self.text_source.addItem("VI Subtitle", "vi_subtitle");self.text_source.addItem("VI Dubbing", "vi_dubbing")
        source_row.addWidget(QLabel("Nguồn nội dung phụ đề"));source_row.addWidget(self.text_source);source_row.addStretch();layout.addLayout(source_row)
        self.source_description = QLabel();self.source_description.setWordWrap(True);layout.addWidget(self.source_description)
        controls = QHBoxLayout(); self.profile = QComboBox()
        for profile, label in PROFILE_LABELS.items(): self.profile.addItem(label, profile.value)
        self.apply_settings = QPushButton("Áp dụng cài đặt")
        self.warning_filter = QComboBox(); self.warning_filter.addItem("Tất cả QC", "ALL"); self.warning_filter.addItem("Có cảnh báo", "WARNINGS")
        for flag in QC_FILTERS: self.warning_filter.addItem(flag, flag)
        controls.addWidget(QLabel("Cấu hình")); controls.addWidget(self.profile); controls.addWidget(self.apply_settings)
        profile_description = QLabel("Cân bằng giữa độ dài, thời lượng và khả năng đọc.")
        profile_description.setWordWrap(True);controls.addWidget(profile_description)
        controls.addStretch(); controls.addWidget(QLabel("Lọc")); controls.addWidget(self.warning_filter); layout.addLayout(controls)
        settings = QHBoxLayout(); form = QFormLayout(); settings.addLayout(form)
        self.preferred_duration = QDoubleSpinBox(); self.preferred_duration.setRange(.1,30); self.preferred_duration.setDecimals(1)
        self.max_duration = QDoubleSpinBox(); self.max_duration.setRange(.1,60); self.max_duration.setDecimals(1)
        self.preferred_syllables = QSpinBox(); self.preferred_syllables.setRange(1,100)
        self.max_syllables = QSpinBox(); self.max_syllables.setRange(1,100)
        self.max_lines = QSpinBox(); self.max_lines.setRange(1,4)
        self.chars_per_line = QSpinBox(); self.chars_per_line.setRange(8,120)
        self.hard_chars_per_line = QSpinBox(); self.hard_chars_per_line.setRange(8,160)
        setting_rows = (
            ("Thời lượng khuyến nghị", self.preferred_duration, "Ưu tiên mỗi đoạn phụ đề có thời lượng gần mức này."),
            ("Thời lượng tối đa", self.max_duration, "Giới hạn thời lượng trước khi xem xét tách."),
            ("Âm tiết khuyến nghị", self.preferred_syllables, "Số âm tiết lý tưởng cho mỗi đoạn; đây là ngưỡng mềm."),
            ("Tối đa âm tiết", self.max_syllables, "Vượt mức này sẽ được ưu tiên tách thành đoạn nhỏ hơn."),
            ("Tối đa số dòng", self.max_lines, "Số dòng phụ đề tối đa hiển thị cùng lúc."),
            ("Ký tự/dòng khuyến nghị", self.chars_per_line, "Ngưỡng mềm cho độ dài một dòng."),
            ("Tối đa ký tự/dòng", self.hard_chars_per_line, "Giới hạn cứng cho độ dài một dòng."),
        )
        for label, control, description in setting_rows:
            row = QWidget();row_layout = QHBoxLayout(row);row_layout.setContentsMargins(0,0,0,0)
            detail = QLabel(description);detail.setWordWrap(True);detail.setMaximumWidth(430)
            row_layout.addWidget(control);row_layout.addWidget(detail, 1)
            form.addRow(label, row)
        settings.addStretch(); layout.addLayout(settings)
        buttons = QHBoxLayout()
        self.auto_all = QPushButton("Auto Segment All"); self.auto_selected = QPushButton("Auto Segment Selected")
        self.refine_audio = QPushButton("Căn timing audio (chọn)")
        self.split_manual = QPushButton("Split Manually"); self.merge = QPushButton("Merge Selected"); self.reset = QPushButton("Reset To Utterance")
        for button in (self.auto_all,self.auto_selected,self.refine_audio,self.split_manual,self.merge,self.reset): buttons.addWidget(button)
        layout.addLayout(buttons)
        self.tree = QTreeWidget(); self.tree.setHeaderLabels(["Utterance / DisplaySegment", "Speaker", "Start", "End", "Vietnamese", "QC"])
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection); self.tree.setColumnWidth(0,170); self.tree.setColumnWidth(4,390)
        self.tree.setWordWrap(True);self.tree.setUniformRowHeights(False);self.tree.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.tree.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.tree.header().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.tree.header().sectionResized.connect(lambda *_:self.tree.doItemsLayout())
        self.search_edit, self.search_clear = add_tree_search(layout, self.tree, (0,1,4), (0,1,4))
        layout.addWidget(self.tree,1)
        self.summary = QLabel(); self.summary.setWordWrap(True); layout.addWidget(self.summary)
        export_group = QGroupBox("EXPORT SUBTITLE")
        export_layout = QVBoxLayout(export_group)
        self.export_subtitle_button = QPushButton("Export Subtitle SRT")
        self.export_subtitle_status = QLabel(); self.export_subtitle_status.setWordWrap(True)
        export_layout.addWidget(self.export_subtitle_button); export_layout.addWidget(self.export_subtitle_status)
        layout.addWidget(export_group)
        self.profile.currentIndexChanged.connect(self.load_profile_defaults)
        self.text_source.currentIndexChanged.connect(self.update_source_description)
        self.update_source_description()

    def update_source_description(self):
        if self.text_source.currentData() == "vi_dubbing":
            self.source_description.setText("Hiển thị đúng nội dung thuyết minh; phụ đề và giọng đọc trùng nhau.")
        else:
            self.source_description.setText("Hiển thị bản dịch đầy đủ; lời TTS có thể khác phụ đề.")

    def load_profile_defaults(self):
        if not self.loading and self.profile.currentData() != SegmentationProfile.CUSTOM.value: self.set_settings(settings_for(self.profile.currentData()))

    def set_settings(self, settings):
        self.active_settings = settings
        self.preferred_duration.setValue((settings.preferred_duration_min + settings.preferred_duration_max) / 2)
        self.max_duration.setValue(settings.max_duration); self.preferred_syllables.setValue(settings.preferred_syllables_max)
        self.max_syllables.setValue(settings.max_syllables); self.max_lines.setValue(settings.max_lines)
        self.chars_per_line.setValue(settings.preferred_chars_per_line); self.hard_chars_per_line.setValue(settings.hard_max_chars_per_line)

    def values(self):
        profile = SegmentationProfile(self.profile.currentData())
        baseline = settings_for(profile) if profile is not SegmentationProfile.CUSTOM else getattr(self, "active_settings", SegmentationSettings())
        preferred_duration = self.preferred_duration.value();max_duration = self.max_duration.value()
        preferred_syllables = self.preferred_syllables.value();max_syllables = self.max_syllables.value()
        preferred_chars = self.chars_per_line.value();hard_chars = self.hard_chars_per_line.value()
        if max_duration < preferred_duration:
            raise ValueError("Thời lượng tối đa phải lớn hơn hoặc bằng Thời lượng khuyến nghị.")
        if max_syllables < preferred_syllables:
            raise ValueError("Tối đa âm tiết phải lớn hơn hoặc bằng Âm tiết khuyến nghị.")
        if hard_chars < preferred_chars:
            raise ValueError("Tối đa ký tự/dòng phải lớn hơn hoặc bằng Ký tự/dòng khuyến nghị.")
        custom = SegmentationSettings(min_duration=baseline.min_duration,
            preferred_duration_min=min(baseline.preferred_duration_min,preferred_duration), preferred_duration_max=preferred_duration,
            max_duration=max_duration,
            preferred_syllables_min=min(baseline.preferred_syllables_min,preferred_syllables), preferred_syllables_max=preferred_syllables,
            max_syllables=max_syllables, max_lines=self.max_lines.value(),
            preferred_chars_per_line=preferred_chars, hard_max_chars_per_line=hard_chars).validate()
        displayed_baseline = (
            (baseline.preferred_duration_min + baseline.preferred_duration_max) / 2,
            baseline.max_duration, baseline.preferred_syllables_max, baseline.max_syllables,
            baseline.max_lines, baseline.preferred_chars_per_line, baseline.hard_max_chars_per_line,
        )
        displayed_current = (preferred_duration, max_duration, preferred_syllables, max_syllables,
                             self.max_lines.value(), preferred_chars, hard_chars)
        if profile is not SegmentationProfile.CUSTOM and displayed_current == displayed_baseline:
            return profile.value, baseline
        return SegmentationProfile.CUSTOM.value, custom

    def load_project(self, project):
        self.loading = True; profile, settings = SubtitleSegmentationService().settings_for(project)
        self.profile.setCurrentIndex(max(0,self.profile.findData(profile.value))); self.set_settings(settings)
        self.text_source.setCurrentIndex(max(0,self.text_source.findData(getattr(project,"subtitle_text_source","vi_subtitle"))))
        self.update_source_description();self.loading = False; self.populate(project)

    def populate(self, project):
        rows = SubtitleSegmentationService().rows(project, self.warning_filter.currentData())
        self.tree.clear(); warning_count = 0
        for utterance, children in rows:
            warning = subtitle_source_warning(project, utterance)
            parent = QTreeWidgetItem([f"Utterance {utterance.id}", utterance.speaker_id, format_srt_timestamp(utterance.start), format_srt_timestamp(utterance.end), subtitle_source_text(project,utterance), warning])
            parent.setData(0,Qt.ItemDataRole.UserRole,"utterance"); parent.setData(1,Qt.ItemDataRole.UserRole,utterance.id); self.tree.addTopLevelItem(parent)
            for segment, flags in children:
                child = QTreeWidgetItem([segment.id, segment.speaker_id, format_srt_timestamp(segment.start), format_srt_timestamp(segment.end), segment.vi_text, " | ".join(flags)])
                child.setData(0,Qt.ItemDataRole.UserRole,"display"); child.setData(1,Qt.ItemDataRole.UserRole,utterance.id); child.setData(2,Qt.ItemDataRole.UserRole,segment.id)
                parent.addChild(child); warning_count += flags != ["OK"]
            parent.setExpanded(True)
        self.summary.setText(f"{len(rows)} utterance hiển thị • {warning_count} DisplaySegment có QC warning")
        apply_tree_search(self.tree)
        self.tree.doItemsLayout()

    def selected_utterance_ids(self):
        return sorted({item.data(1,Qt.ItemDataRole.UserRole) for item in self.tree.selectedItems() if item.data(0,Qt.ItemDataRole.UserRole) in ("utterance","display")})

    def selected_display_refs(self):
        return [(item.data(1,Qt.ItemDataRole.UserRole),item.data(2,Qt.ItemDataRole.UserRole)) for item in self.tree.selectedItems() if item.data(0,Qt.ItemDataRole.UserRole) == "display"]


def build(): return SubtitlePage()
