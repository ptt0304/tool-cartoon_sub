from PySide6.QtWidgets import (QPushButton, QGroupBox, QVBoxLayout, QHBoxLayout,
    QLabel, QComboBox, QLineEdit, QTableWidgetItem)
from .common import page, table
from cartoon_sub.subtitle.timestamps import format_srt_timestamp
from cartoon_sub.ui.table_search import add_table_search, apply_table_search
from cartoon_sub.ui.cache_status_widget import CacheStatusBox
from cartoon_sub.transcription.mode import MODE_LABELS, TranscriptSourceMode, normalize_mode
from cartoon_sub.transcription.video.visual_detector import COARSE_FPS, REFINE_FPS


def populate(widget, project):
    target = widget.table
    target.setRowCount(len(project.segments))
    for row, segment in enumerate(project.segments):
        values = [segment.id, format_srt_timestamp(segment.start), format_srt_timestamp(segment.end),
                  f"{segment.duration:.3f}", segment.zh, segment.transcript_language_status]
        for column, value in enumerate(values):
            item = QTableWidgetItem(str(value))
            item.setToolTip(str(value))
            target.setItem(row, column, item)
    apply_table_search(target)
    target.resizeRowsToContents()

_AUDIO_ONLY = ("stt_label", "stt_model_search", "stt_model", "stt_model_effective",
               "ai_label", "ai_model_search", "ai_model", "ai_model_effective",
               "reset_ai_button", "semantic_status", "qa_status", "audio_cache_clear_button")
_VIDEO_ONLY = ("video_mode_hint", "video_model_label", "video_model_search", "video_model",
               "video_model_effective", "roi_button", "video_cache_clear_button", "video_qaqc_button",
               "video_qaqc_undo_button", "video_qaqc_status")
_IMPORT_ONLY = ("import_hint", "import_button")


def apply_mode(widget, mode):
    """Show the controls of the selected acquisition mode; Audio widgets are only hidden, never rebuilt."""
    video = mode == TranscriptSourceMode.VIDEO.value
    importing = mode == TranscriptSourceMode.IMPORT_SRT.value
    for name in _AUDIO_ONLY:
        getattr(widget, name).setVisible(not video and not importing)
    for name in _VIDEO_ONLY:
        getattr(widget, name).setVisible(video)
    for name in _IMPORT_ONLY:
        getattr(widget, name).setVisible(importing)
    widget.transcribe_button.setVisible(not importing)
    widget.transcribe_button.setText("Run Video Transcript" if video else "Run Chinese Transcript")


def set_mode(widget, mode):
    """Programmatic mode update (project load); does not emit currentIndexChanged."""
    mode = normalize_mode(mode)
    widget.mode_combo.blockSignals(True)
    widget.mode_combo.setCurrentIndex(max(0, widget.mode_combo.findData(mode)))
    widget.mode_combo.blockSignals(False)
    apply_mode(widget, mode)


def video_status_text(detection=None):
    """Render the detector values from the production constants/manifest, never a UI-only default."""
    detection = detection or {}
    coarse = float(detection.get("coarse_fps", COARSE_FPS))
    refine = float(detection.get("refine_fps", REFINE_FPS))
    count = detection.get("candidate_events")
    if count is None:
        return f"Chế độ Video: phát hiện cục bộ {coarse:.1f} fps, refine {refine:.1f} fps; sau đó OCR theo workload."
    return (f"Chế độ Video: đã phát hiện {count} sự kiện hình ảnh phụ đề cục bộ "
            f"({coarse:.1f} fps, refine {refine:.1f} fps). Chưa OCR hoặc tạo transcript.")


def build():
    widget, layout = page("Chọn nguồn transcript: Audio, Video hoặc Import Chinese SRT. Chỉ hành động Run/Import mới thay đổi Master Timeline.")
    widget.import_button = QPushButton("Import Chinese SRT")
    mode_row = QHBoxLayout(); widget.mode_combo = QComboBox()
    for mode in (TranscriptSourceMode.AUDIO.value, TranscriptSourceMode.VIDEO.value,
                 TranscriptSourceMode.IMPORT_SRT.value):
        widget.mode_combo.addItem(MODE_LABELS[mode], mode)
    mode_row.addWidget(QLabel("Nguồn transcript")); mode_row.addWidget(widget.mode_combo, 1)
    layout.addLayout(mode_row)
    widget.video_mode_hint = QLabel(video_status_text())
    widget.video_mode_hint.setWordWrap(True); widget.video_mode_hint.hide()
    layout.addWidget(widget.video_mode_hint)
    widget.roi_button = QPushButton("Chọn vùng phụ đề Video…")
    widget.roi_button.hide()
    layout.addWidget(widget.roi_button)
    video_row = QHBoxLayout(); widget.video_model_search = QLineEdit(); widget.video_model = QComboBox()
    widget.video_model_search.setPlaceholderText("Tìm model Video / OCR theo provider, tên hoặc ID…")
    widget.video_model_effective = QLabel(); widget.video_model_label = QLabel("Model Video / OCR")
    video_row.addWidget(widget.video_model_label); video_row.addWidget(widget.video_model_search, 1)
    video_row.addWidget(widget.video_model, 2); video_row.addWidget(widget.video_model_effective)
    layout.addLayout(video_row)
    stt_row = QHBoxLayout(); widget.stt_model_search = QLineEdit(); widget.stt_model = QComboBox()
    widget.stt_model_search.setPlaceholderText("Tìm model STT theo provider, tên hoặc ID…")
    widget.stt_model_effective = QLabel(); widget.stt_label = QLabel("Model STT")
    stt_row.addWidget(widget.stt_label); stt_row.addWidget(widget.stt_model_search, 1)
    stt_row.addWidget(widget.stt_model, 2); stt_row.addWidget(widget.stt_model_effective)
    layout.addLayout(stt_row)
    model_row = QHBoxLayout(); widget.ai_model_search = QLineEdit(); widget.ai_model = QComboBox(); widget.ai_model_effective = QLabel()
    widget.ai_model_search.setPlaceholderText("Tìm model AI theo provider, tên hoặc ID…")
    widget.ai_label = QLabel("AI xử lý ranh giới câu mơ hồ")
    model_row.addWidget(widget.ai_label); model_row.addWidget(widget.ai_model_search, 1); model_row.addWidget(widget.ai_model, 2)
    widget.reset_ai_button = QPushButton("Xóa dữ liệu AI / Chạy lại")
    model_row.addWidget(widget.ai_model_effective); model_row.addWidget(widget.reset_ai_button); layout.addLayout(model_row)
    widget.semantic_status = QLabel("AI Semantic Boundary: Chưa chạy transcript.")
    widget.semantic_status.setWordWrap(True); widget.semantic_status.setTextInteractionFlags(widget.semantic_status.textInteractionFlags())
    layout.addWidget(widget.semantic_status)
    widget.qa_status = QLabel("Transcript QA: Chưa chạy transcript.")
    widget.qa_status.setWordWrap(True); widget.qa_status.setTextInteractionFlags(widget.qa_status.textInteractionFlags())
    layout.addWidget(widget.qa_status)
    widget.cache_status = CacheStatusBox(); layout.addWidget(widget.cache_status)
    widget.audio_cache_clear_button = QPushButton("Xóa cache Audio Transcript / Chạy lại")
    widget.video_cache_clear_button = QPushButton("Xóa cache Video / Chạy lại")
    widget.video_qaqc_button = QPushButton("QA/QC transcript từ video")
    widget.video_qaqc_undo_button = QPushButton("Hủy QA/QC")
    widget.video_qaqc_status = QLabel("QA/QC Video: Chưa áp dụng.")
    widget.video_qaqc_status.setWordWrap(True)
    layout.addWidget(widget.audio_cache_clear_button)
    layout.addWidget(widget.video_cache_clear_button)
    qa_row = QHBoxLayout(); qa_row.addWidget(widget.video_qaqc_button); qa_row.addWidget(widget.video_qaqc_undo_button)
    layout.addLayout(qa_row); layout.addWidget(widget.video_qaqc_status)
    widget.import_hint = QLabel("Import Chinese SRT không gọi AI. Chọn file SRT để tạo Chinese Master Timeline.")
    widget.import_hint.setWordWrap(True); layout.addWidget(widget.import_hint)
    widget.transcribe_button = QPushButton("Run Chinese Transcript")
    widget.table = table(["ID", "Start", "End", "Duration", "Chinese", "Language status"])
    for control in (widget.import_button, widget.transcribe_button):
        layout.addWidget(control)
    apply_mode(widget, TranscriptSourceMode.AUDIO.value)
    widget.search_edit, widget.search_clear = add_table_search(layout, widget.table, (0, 4, 5))
    layout.addWidget(widget.table)
    export_group = QGroupBox("EXPORT TRANSCRIPT")
    export_layout = QVBoxLayout(export_group)
    widget.export_transcript_button = QPushButton("Export Transcript SRT")
    widget.export_transcript_status = QLabel()
    widget.export_transcript_status.setWordWrap(True)
    export_layout.addWidget(widget.export_transcript_button)
    export_layout.addWidget(widget.export_transcript_status)
    layout.addWidget(export_group)
    return widget
