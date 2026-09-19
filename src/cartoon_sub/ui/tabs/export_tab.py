from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)


class ExportPage(QWidget):
    render_requested = Signal(bool, float)  # (is_test_30s, start_time)

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)

        description = QLabel(
            "Export video và subtitle từ master timeline. Video render sẽ sử dụng file audio/final_audio.wav "
            "kết hợp với hình ảnh, mask và phụ đề."
        )
        description.setWordWrap(True)
        layout.addWidget(description)

        # ----------------------------------------------------
        # Video Render Section
        # ----------------------------------------------------
        video_group = QGroupBox("VIDEO EXPORT")
        v_layout = QVBoxLayout(video_group)

        self.mode_group = QButtonGroup(self)
        self.radio_test = QRadioButton("Test 30 seconds (với độ trễ offset âm thanh chuẩn)")
        self.radio_full = QRadioButton("Full Video")
        self.radio_test.setChecked(True)
        self.mode_group.addButton(self.radio_test)
        self.mode_group.addButton(self.radio_full)

        v_layout.addWidget(self.radio_test)

        test_row = QHBoxLayout()
        test_row.setContentsMargins(20, 0, 0, 0)
        test_row.addWidget(QLabel("Vị trí bắt đầu (giây):"))
        self.test_start_spin = QDoubleSpinBox()
        self.test_start_spin.setRange(0.0, 86400.0)
        self.test_start_spin.setSingleStep(5.0)
        self.test_start_spin.setDecimals(3)
        self.test_start_spin.setValue(0.0)
        test_row.addWidget(self.test_start_spin)
        self.test_duration_label = QLabel("Thời lượng: tối đa 30s")
        test_row.addWidget(self.test_duration_label)
        test_row.addStretch()
        v_layout.addLayout(test_row)

        v_layout.addWidget(self.radio_full)

        self.final_audio_status = QLabel("Audio: chưa kiểm tra")
        self.final_audio_status.setWordWrap(True)
        v_layout.addWidget(self.final_audio_status)

        self.render_button = QPushButton("Render Video")
        self.render_button.setStyleSheet("font-weight: bold; padding: 6px;")
        v_layout.addWidget(self.render_button)

        self.render_output_label = QLabel()
        self.render_output_label.setWordWrap(True)
        v_layout.addWidget(self.render_output_label)

        layout.addWidget(video_group)

        # ----------------------------------------------------
        # Subtitle / Text Manifest Export Section
        # ----------------------------------------------------
        sub_group = QGroupBox("SUBTITLE & TEXT EXPORT")
        s_layout = QVBoxLayout(sub_group)
        self.text_type = QComboBox()
        self.text_type.addItem("Vietnamese Dubbing", "vi_dubbing")
        self.text_type.addItem("Vietnamese Subtitle", "vi_subtitle")
        self.export_button = QPushButton("Export SRT + TXT/manifest theo speaker")
        self.path_label = QLabel()
        self.path_label.setWordWrap(True)
        s_layout.addWidget(self.text_type)
        s_layout.addWidget(self.export_button)
        s_layout.addWidget(self.path_label)
        layout.addWidget(sub_group)

        layout.addStretch()

        self._root = None
        self._project = None

        self.radio_test.toggled.connect(self._on_mode_toggled)
        self.render_button.clicked.connect(self._on_render_clicked)

    def _on_mode_toggled(self, checked):
        self.test_start_spin.setEnabled(self.radio_test.isChecked())

    def _on_render_clicked(self):
        is_test = self.radio_test.isChecked()
        start = float(self.test_start_spin.value()) if is_test else 0.0
        self.render_requested.emit(is_test, start)

    def populate(self, project, project_dir=None):
        self._project = project
        self._root = Path(project_dir) if project_dir else None

        duration = float(project.metadata.get("duration", 0.0))
        self.test_start_spin.setMaximum(max(0.0, duration))

        final = self._root / "audio" / "final_audio.wav" if self._root else None
        f_status = getattr(project, "final_audio_status", "not_generated")

        if not final or not final.is_file():
            self.final_audio_status.setText("⚠️ Chưa tạo audio/final_audio.wav! Vui lòng sang tab Audio bấm 'Build Final Audio' trước.")
            self.render_button.setEnabled(False)
        elif f_status == "stale":
            self.final_audio_status.setText("⚠️ audio/final_audio.wav bị STALE (cấu hình audio đã thay đổi)! Hãy sang tab Audio tạo lại trước.")
            self.render_button.setEnabled(False)
        else:
            self.final_audio_status.setText(f"✓ Audio sẵn sàng: {final.name}")
            self.render_button.setEnabled(True)

    def reset_media(self):
        pass


def build():
    return ExportPage()
