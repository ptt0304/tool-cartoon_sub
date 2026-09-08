from PySide6.QtWidgets import (QDialog,QFormLayout,QComboBox,QDoubleSpinBox,QSpinBox,QCheckBox,QDialogButtonBox,QLabel)
from cartoon_sub.syllable.target import DubbingSettings
from cartoon_sub.translation.modes import MODE_LABELS

class DubbingSettingsDialog(QDialog):
    def __init__(self,settings,parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings > Translation / Dubbing")
        self.resize(650,430)
        layout=QFormLayout(self)
        self.mode=QComboBox()
        for key,label in MODE_LABELS.items(): self.mode.addItem(label,key)
        self.mode.setCurrentIndex(self.mode.findData(settings.mode))
        self.strategy=QComboBox()
        for key,label in [("time_based","Theo thời lượng"),("chinese_count","Theo âm tiết Trung"),("hybrid","Kết hợp")]: self.strategy.addItem(label,key)
        self.strategy.setCurrentIndex(self.strategy.findData(settings.target_strategy))
        self.rate=QDoubleSpinBox(); self.rate.setRange(.5,12); self.rate.setValue(settings.speech_rate)
        self.weight=QDoubleSpinBox(); self.weight.setRange(0,1); self.weight.setSingleStep(.1); self.weight.setValue(settings.hybrid_time_weight)
        self.kind=QComboBox(); self.kind.addItem("Phần trăm","percent"); self.kind.addItem("Số âm tiết","syllables"); self.kind.setCurrentIndex(self.kind.findData(settings.tolerance_kind))
        self.percent=QDoubleSpinBox(); self.percent.setRange(0,100); self.percent.setValue(settings.tolerance_percent)
        self.syllables=QSpinBox(); self.syllables.setRange(0,20); self.syllables.setValue(settings.tolerance_syllables)
        self.retry=QSpinBox(); self.retry.setRange(0,5); self.retry.setValue(settings.strict_retry)
        self.separate=QCheckBox("Giữ Subtitle và Dubbing riêng"); self.separate.setChecked(settings.separate_texts)
        for label,control in [("Mode mặc định",self.mode),("Target strategy",self.strategy),("Tốc độ VI (âm tiết/giây)",self.rate),("Trọng số thời lượng (Hybrid)",self.weight),("Tolerance dùng",self.kind),("Tolerance %",self.percent),("Tolerance ± âm tiết",self.syllables),("Strict rewrite tối đa",self.retry),("",self.separate)]: layout.addRow(label,control)
        note=QLabel("Áp dụng cấu hình này cho project hiện tại và lưu mặc định. Mode áp dụng cho tất cả câu; có thể đổi từng câu trong bảng. Bỏ chọn tách hai bản sẽ cho phép tối ưu thay cả subtitle. Speech rate là ước lượng, không bảo đảm thời lượng audio TTS.")
        note.setWordWrap(True); layout.addRow(note)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); layout.addRow(buttons)

    def values(self):
        return DubbingSettings(mode=self.mode.currentData(),target_strategy=self.strategy.currentData(),speech_rate=self.rate.value(),tolerance_percent=self.percent.value(),tolerance_syllables=self.syllables.value(),tolerance_kind=self.kind.currentData(),strict_retry=self.retry.value(),separate_texts=self.separate.isChecked(),hybrid_time_weight=self.weight.value()).validate()
