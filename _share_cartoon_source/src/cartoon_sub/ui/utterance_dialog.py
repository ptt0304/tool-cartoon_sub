from PySide6.QtWidgets import QDialog,QFormLayout,QPlainTextEdit,QLabel,QComboBox,QSpinBox,QDialogButtonBox
from cartoon_sub.translation.modes import MODE_LABELS
from cartoon_sub.syllable.vietnamese import count_syllables

class UtteranceDialog(QDialog):
    def __init__(self,segment,parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Utterance {segment.id} — {segment.speaker_id}")
        self.resize(700,520)
        box=QFormLayout(self)
        zh=QPlainTextEdit(segment.zh); zh.setReadOnly(True); zh.setMaximumHeight(80); box.addRow("Chinese",zh)
        self.subtitle=QPlainTextEdit(segment.vi_subtitle)
        self.dubbing=QPlainTextEdit(segment.vi_dubbing)
        box.addRow("Vietnamese Subtitle",self.subtitle); box.addRow("Vietnamese Dubbing",self.dubbing)
        self.counts=QLabel(); box.addRow(self.counts)
        self.mode=QComboBox()
        for key,label in MODE_LABELS.items(): self.mode.addItem(label,key)
        self.mode.setCurrentIndex(self.mode.findData(segment.translation_mode)); box.addRow("Mode",self.mode)
        self.target=QSpinBox(); self.target.setRange(0,9999); self.target.setSpecialValueText("Tự tính theo Settings"); self.target.setValue(segment.target_override or 0); box.addRow("Target override",self.target)
        note=QLabel("Sửa bản dubbing không thay bản subtitle. Mode khớp/ngắn có thể nén nghĩa. Timestamp giữ nguyên."); note.setWordWrap(True); box.addRow(note)
        self.subtitle.textChanged.connect(self.refresh_counts); self.dubbing.textChanged.connect(self.refresh_counts)
        self.refresh_counts()
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); box.addRow(buttons)
    def refresh_counts(self):
        self.counts.setText(f"Âm tiết local — Subtitle: {count_syllables(self.subtitle.toPlainText())}; Dubbing: {count_syllables(self.dubbing.toPlainText())}")
