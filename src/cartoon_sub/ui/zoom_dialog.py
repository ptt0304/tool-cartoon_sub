from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout


class ZoomDialog(QDialog):
    def __init__(self, manager, apply_zoom, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.apply_zoom = apply_zoom
        self.setWindowTitle("Settings > Giao diện")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Thu phóng giao diện (0% = mức an toàn 50%, 100% = kích thước gốc)"))
        row = QHBoxLayout()
        self.minus = QPushButton("−")
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setSingleStep(5)
        self.slider.setPageStep(5)
        self.slider.setValue(manager.percent)
        self.plus = QPushButton("+")
        self.value_label = QLabel(f"{manager.percent}%")
        self.value_label.setMinimumWidth(48)
        row.addWidget(self.minus)
        row.addWidget(self.slider, 1)
        row.addWidget(self.plus)
        row.addWidget(self.value_label)
        layout.addLayout(row)
        close = QPushButton("Đóng")
        close.clicked.connect(self.accept)
        layout.addWidget(close)
        self.minus.clicked.connect(lambda: self.slider.setValue(self.slider.value() - 5))
        self.plus.clicked.connect(lambda: self.slider.setValue(self.slider.value() + 5))
        self.slider.valueChanged.connect(self._changed)

    def _changed(self, value):
        self.value_label.setText(f"{value}%")
        self.apply_zoom(value)
