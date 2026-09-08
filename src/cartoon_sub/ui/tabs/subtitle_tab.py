from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QFormLayout,QLabel,QPushButton,QComboBox,
    QDoubleSpinBox,QSpinBox,QTreeWidget,QTreeWidgetItem,QAbstractItemView)

from cartoon_sub.subtitle.segmentation import SegmentationProfile, SegmentationSettings, settings_for
from cartoon_sub.subtitle.segmentation_service import SubtitleSegmentationService


PROFILE_LABELS = {
    SegmentationProfile.BALANCED: "Balanced", SegmentationProfile.READING_COMFORT: "Reading comfort",
    SegmentationProfile.FAST_DIALOGUE: "Fast dialogue", SegmentationProfile.PRESERVE_SENTENCES: "Preserve sentences",
    SegmentationProfile.CUSTOM: "Custom",
}
QC_FILTERS = ("OK", "TOO_LONG", "TOO_SHORT", "TOO_MANY_SYLLABLES", "TOO_MANY_LINES",
              "HIGH_READING_SPEED", "BAD_SPLIT", "MANUAL_REVIEW")


class SubtitlePage(QWidget):
    def __init__(self):
        super().__init__(); self.loading = False
        layout = QVBoxLayout(self)
        note = QLabel("Utterance là lời thoại nguồn; DisplaySegment là cách hiển thị. Auto Segment không gọi Gemini, "
                      "không sửa transcript/dịch và giữ timestamp overlap giữa speaker.")
        note.setWordWrap(True); layout.addWidget(note)
        controls = QHBoxLayout(); self.profile = QComboBox()
        for profile, label in PROFILE_LABELS.items(): self.profile.addItem(label, profile.value)
        self.apply_settings = QPushButton("Áp dụng settings")
        self.warning_filter = QComboBox(); self.warning_filter.addItem("Tất cả QC", "ALL"); self.warning_filter.addItem("Có cảnh báo", "WARNINGS")
        for flag in QC_FILTERS: self.warning_filter.addItem(flag, flag)
        controls.addWidget(QLabel("Profile")); controls.addWidget(self.profile); controls.addWidget(self.apply_settings)
        controls.addStretch(); controls.addWidget(QLabel("Lọc")); controls.addWidget(self.warning_filter); layout.addLayout(controls)
        settings = QHBoxLayout(); form = QFormLayout(); settings.addLayout(form)
        self.preferred_duration = QDoubleSpinBox(); self.preferred_duration.setRange(.1,30); self.preferred_duration.setDecimals(1)
        self.max_duration = QDoubleSpinBox(); self.max_duration.setRange(.1,60); self.max_duration.setDecimals(1)
        self.preferred_syllables = QSpinBox(); self.preferred_syllables.setRange(1,100)
        self.max_syllables = QSpinBox(); self.max_syllables.setRange(1,100)
        self.max_lines = QSpinBox(); self.max_lines.setRange(1,4)
        self.chars_per_line = QSpinBox(); self.chars_per_line.setRange(8,120)
        self.hard_chars_per_line = QSpinBox(); self.hard_chars_per_line.setRange(8,160)
        for label, control in (("Preferred duration",self.preferred_duration),("Max duration",self.max_duration),
                               ("Preferred syllables",self.preferred_syllables),("Max syllables",self.max_syllables),
                               ("Max lines",self.max_lines),("Preferred chars / line",self.chars_per_line),
                               ("Hard chars / line",self.hard_chars_per_line)): form.addRow(label,control)
        settings.addStretch(); layout.addLayout(settings)
        buttons = QHBoxLayout()
        self.auto_all = QPushButton("Auto Segment All"); self.auto_selected = QPushButton("Auto Segment Selected")
        self.split_manual = QPushButton("Split Manually"); self.merge = QPushButton("Merge Selected"); self.reset = QPushButton("Reset To Utterance")
        for button in (self.auto_all,self.auto_selected,self.split_manual,self.merge,self.reset): buttons.addWidget(button)
        layout.addLayout(buttons)
        self.tree = QTreeWidget(); self.tree.setHeaderLabels(["Utterance / DisplaySegment", "Speaker", "Start", "End", "Vietnamese", "QC"])
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection); self.tree.setColumnWidth(0,170); self.tree.setColumnWidth(4,390)
        layout.addWidget(self.tree,1)
        self.summary = QLabel(); self.summary.setWordWrap(True); layout.addWidget(self.summary)
        self.profile.currentIndexChanged.connect(self.load_profile_defaults)

    def load_profile_defaults(self):
        if not self.loading and self.profile.currentData() != SegmentationProfile.CUSTOM.value: self.set_settings(settings_for(self.profile.currentData()))

    def set_settings(self, settings):
        self.preferred_duration.setValue((settings.preferred_duration_min + settings.preferred_duration_max) / 2)
        self.max_duration.setValue(settings.max_duration); self.preferred_syllables.setValue(settings.preferred_syllables_max)
        self.max_syllables.setValue(settings.max_syllables); self.max_lines.setValue(settings.max_lines)
        self.chars_per_line.setValue(settings.preferred_chars_per_line); self.hard_chars_per_line.setValue(settings.hard_max_chars_per_line)

    def values(self):
        profile = SegmentationProfile(self.profile.currentData())
        baseline = settings_for(profile) if profile is not SegmentationProfile.CUSTOM else SegmentationSettings()
        custom = SegmentationSettings(min_duration=baseline.min_duration,
            preferred_duration_min=min(baseline.preferred_duration_min,self.preferred_duration.value()), preferred_duration_max=self.preferred_duration.value(),
            max_duration=max(self.max_duration.value(),self.preferred_duration.value()),
            preferred_syllables_min=min(baseline.preferred_syllables_min,self.preferred_syllables.value()), preferred_syllables_max=self.preferred_syllables.value(),
            max_syllables=max(self.max_syllables.value(),self.preferred_syllables.value()), max_lines=self.max_lines.value(),
            preferred_chars_per_line=self.chars_per_line.value(), hard_max_chars_per_line=max(self.hard_chars_per_line.value(),self.chars_per_line.value())).validate()
        return profile.value, custom

    def load_project(self, project):
        self.loading = True; profile, settings = SubtitleSegmentationService().settings_for(project)
        self.profile.setCurrentIndex(max(0,self.profile.findData(profile.value))); self.set_settings(settings); self.loading = False; self.populate(project)

    def populate(self, project):
        rows = SubtitleSegmentationService().rows(project, self.warning_filter.currentData())
        self.tree.clear(); warning_count = 0
        for utterance, children in rows:
            parent = QTreeWidgetItem([f"Utterance {utterance.id}", utterance.speaker_id, f"{utterance.start:.3f}", f"{utterance.end:.3f}", utterance.vi_subtitle, ""])
            parent.setData(0,Qt.ItemDataRole.UserRole,"utterance"); parent.setData(1,Qt.ItemDataRole.UserRole,utterance.id); self.tree.addTopLevelItem(parent)
            for segment, flags in children:
                child = QTreeWidgetItem([segment.id, segment.speaker_id, f"{segment.start:.3f}", f"{segment.end:.3f}", segment.vi_text, " | ".join(flags)])
                child.setData(0,Qt.ItemDataRole.UserRole,"display"); child.setData(1,Qt.ItemDataRole.UserRole,utterance.id); child.setData(2,Qt.ItemDataRole.UserRole,segment.id)
                parent.addChild(child); warning_count += flags != ["OK"]
            parent.setExpanded(True)
        self.summary.setText(f"{len(rows)} utterance hiển thị • {warning_count} DisplaySegment có QC warning")

    def selected_utterance_ids(self):
        return sorted({item.data(1,Qt.ItemDataRole.UserRole) for item in self.tree.selectedItems() if item.data(0,Qt.ItemDataRole.UserRole) in ("utterance","display")})

    def selected_display_refs(self):
        return [(item.data(1,Qt.ItemDataRole.UserRole),item.data(2,Qt.ItemDataRole.UserRole)) for item in self.tree.selectedItems() if item.data(0,Qt.ItemDataRole.UserRole) == "display"]


def build(): return SubtitlePage()
