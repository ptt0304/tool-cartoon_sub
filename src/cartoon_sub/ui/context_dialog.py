from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPlainTextEdit, QTabWidget,
    QWidget, QFormLayout, QTableWidget, QTableWidgetItem, QPushButton, QMessageBox, QHeaderView,
    QFileDialog)
import json
import logging
from pathlib import Path
from cartoon_sub.translation.context_models import StoryContext
from cartoon_sub.translation.context_export import export_context_ai_json


logger = logging.getLogger(__name__)
GENDER_DISPLAY = {"male": "Nam", "female": "Nữ", "unknown": "Chưa xác định"}
GENDER_CANONICAL = {value.casefold(): key for key, value in GENDER_DISPLAY.items()}


class ContextDialog(QDialog):
    """The model's proposal never silently replaces the applied context."""
    def __init__(self, context, valid_ids, parent=None, proposal=False, visual_ready=True,
                 speaker_reviewed=True, diagnostic_payload=None, export_directory=None):
        super().__init__(parent)
        self.valid_ids = valid_ids
        self.result_context = None
        self.diagnostic_payload = diagnostic_payload or {}
        self.export_directory = Path(export_directory) if export_directory else Path.cwd()
        self.setWindowTitle("Duyệt & lưu ngữ cảnh AI" if proposal else "Ngữ cảnh AI đã duyệt")
        self.resize(1050, 720)
        layout = QVBoxLayout(self)
        note = QLabel("Kiểm tra và sửa tên, quan hệ, thuật ngữ, xưng hô cùng mọi suy luận của AI. Để trống điều chưa rõ; "
                      "ID bằng chứng là số dòng Transcript (ngăn bằng dấu phẩy). Bản được lưu sẽ là nguồn ngữ cảnh chính cho các lần dịch sau.")
        note.setWordWrap(True)
        layout.addWidget(note)
        tabs = QTabWidget()
        layout.addWidget(tabs)
        overview = QWidget()
        form = QFormLayout(overview)
        self.fields = {}
        for key, label in (("setting", "Bối cảnh"), ("summary", "Tóm tắt"), ("narration", "Ngôi kể / giọng kể"),
                           ("uncertainties", "Chỗ chưa chắc — mỗi dòng một ý")):
            edit = QPlainTextEdit()
            value = context.get(key, "")
            edit.setPlainText("\n".join(value) if isinstance(value, list) else value)
            self.fields[key] = edit
            form.addRow(label, edit)
        tabs.addTab(overview, "Tổng quan")
        self.tables = {}
        specs = [("characters", "Nhân vật", ["source", "target", "notes", "evidence_ids"],
                  ["Tên Trung / biệt danh", "Tên Việt", "Thân phận, quan hệ, ghi chú", "ID bằng chứng"]),
                 ("terms", "Thuật ngữ", ["source", "target", "notes", "evidence_ids"],
                  ["Thuật ngữ Trung", "Cách dịch Việt", "Nghĩa / điều kiện dùng", "ID bằng chứng"]),
                 ("address_rules", "Xưng hô", ["speaker", "listener", "self_term", "address_term", "condition", "evidence_ids"],
                  ["Người nói", "Người nghe", "Xưng", "Gọi", "Tình huống / thời điểm", "ID bằng chứng"]),
                 ("character_profiles", "Nhân vật từ video", ["character_id", "name", "role", "gender_context", "relationships", "visual_description", "associated_speakers", "confidence", "evidence_ids"],
                  ["ID nhân vật", "Tên / biệt danh", "Vai trò", "Giới tính theo ngữ cảnh", "Quan hệ", "Mô tả hình ảnh", "SPK liên kết", "Độ tin cậy", "ID bằng chứng"]),
                 ("speaker_character_mappings", "SPK → nhân vật", ["spk_id", "character_id", "confidence", "evidence_ids", "notes"],
                  ["SPK", "ID nhân vật", "Độ tin cậy", "ID bằng chứng", "Ghi chú"])]
        for key, title, keys, labels in specs:
            page = QWidget()
            box = QVBoxLayout(page)
            table = QTableWidget(0, len(keys))
            table.setHorizontalHeaderLabels(labels)
            table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
            for row in context.get(key, []):
                index = table.rowCount()
                table.insertRow(index)
                for col, name in enumerate(keys):
                    value = row.get(name, "")
                    if key == "characters" and name == "target" and str(value).startswith("CHAR_"):
                        value = "Chưa xác định"
                    if key == "terms" and name == "target" and str(value).startswith("TERM_"):
                        value = "Chưa xác định"
                    if name == "gender_context":
                        value = GENDER_DISPLAY.get(str(value), value)
                    shown = ", ".join(map(str, value)) if isinstance(value, list) else str(value)
                    table.setItem(index, col, QTableWidgetItem(shown))
            box.addWidget(table)
            buttons = QHBoxLayout()
            add, remove = QPushButton("Thêm dòng"), QPushButton("Xóa dòng chọn")
            add.clicked.connect(lambda checked=False, t=table: t.insertRow(t.rowCount()))
            remove.clicked.connect(lambda checked=False, t=table: t.removeRow(t.currentRow()) if t.currentRow() >= 0 else None)
            buttons.addWidget(add)
            buttons.addWidget(remove)
            box.addLayout(buttons)
            list_fields = {name for name in ("relationships", "associated_speakers") if name in keys}
            self.tables[key] = (table, keys, list_fields)
            tabs.addTab(page, title)
        visual_page = QWidget()
        visual_layout = QVBoxLayout(visual_page)
        visual_help = QLabel("Ngữ cảnh hình ảnh theo từng ID. Có thể sửa người nói, người nghe, đối tượng được nhắc tới, "
                             "nhân vật xuất hiện, chế độ cảnh, độ tin cậy và trạng thái trong JSON. "
                             "Giữ nguyên tên khóa và enum nội bộ; dùng UNKNOWN khi chưa đủ bằng chứng.")
        visual_help.setWordWrap(True)
        self.visual_json = QPlainTextEdit()
        self.visual_json.setPlainText(json.dumps(context.get("visual_contexts", []), ensure_ascii=False, indent=2))
        visual_layout.addWidget(visual_help)
        visual_layout.addWidget(self.visual_json)
        tabs.addTab(visual_page, "Visual theo ID")
        buttons = QHBoxLayout()
        cancel = QPushButton("Đóng, chưa lưu")
        self.export_button = QPushButton("Export context_ai.json")
        self.apply_button = QPushButton(
            "Phê duyệt & lưu ngữ cảnh" if proposal else "Lưu thay đổi ngữ cảnh đã duyệt")
        cancel.clicked.connect(self.reject)
        self.export_button.clicked.connect(self.export_diagnostic)
        self.apply_button.clicked.connect(self.apply)
        buttons.addWidget(cancel)
        buttons.addWidget(self.export_button)
        self.save_status = QLabel("")
        if not speaker_reviewed:
            self.save_status.setText("Có thể xem/sửa Candidate, nhưng cần hoàn tất Speaker Review trước khi phê duyệt.")
            self.apply_button.setEnabled(False)
        elif not visual_ready or not context.get("visual_contexts"):
            self.save_status.setText("Chưa thể lưu — chưa phân tích video thành công.")
            self.apply_button.setEnabled(False)
        buttons.addWidget(self.save_status)
        buttons.addWidget(self.apply_button)
        layout.addLayout(buttons)

    def export_diagnostic(self):
        default = str(self.export_directory / "context_ai.json")
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Context AI JSON", default, "JSON (*.json)")
        if not path:
            return
        try:
            export_context_ai_json(self.diagnostic_payload, path)
            QMessageBox.information(self, "Export Context AI", f"Đã xuất:\n{path}")
        except (OSError, TypeError, ValueError) as exc:
            QMessageBox.warning(self, "Export Context AI", f"Không thể xuất JSON:\n{exc}")

    def values(self):
        data = {key: field.toPlainText().strip() for key, field in self.fields.items()}
        data["uncertainties"] = [line.strip() for line in data["uncertainties"].splitlines() if line.strip()]
        for key, (table, keys, list_fields) in self.tables.items():
            rows = []
            for index in range(table.rowCount()):
                row = {name: (table.item(index, col).text().strip() if table.item(index, col) else "")
                       for col, name in enumerate(keys)}
                if not any(row.values()):
                    continue
                if "evidence_ids" in row:
                    try:
                        row["evidence_ids"] = [int(v.strip()) for v in row["evidence_ids"].split(",") if v.strip()]
                    except ValueError:
                        raise ValueError(f"ID bằng chứng ở dòng {index + 1} phải là số, ngăn bằng dấu phẩy") from None
                for field in list_fields:
                    row[field] = [value.strip() for value in row[field].split(",") if value.strip()]
                if "gender_context" in row:
                    row["gender_context"] = GENDER_CANONICAL.get(
                        row["gender_context"].casefold(), row["gender_context"].casefold()
                    )
                if "confidence" in row:
                    try:
                        row["confidence"] = float(row["confidence"])
                    except ValueError:
                        raise ValueError(f"Confidence ở dòng {index + 1} phải là số 0–1") from None
                rows.append(row)
            data[key] = rows
        try:
            data["visual_contexts"] = json.loads(self.visual_json.toPlainText() or "[]")
        except json.JSONDecodeError as exc:
            raise ValueError(f"Visual theo ID không phải JSON hợp lệ: dòng {exc.lineno}, cột {exc.colno}: {exc.msg}") from None
        if not data["visual_contexts"]:
            raise ValueError("Không thể lưu: Visual Context chưa được phân tích từ video.")
        validated = StoryContext.from_dict(data, self.valid_ids).to_dict()
        visual_ids = {row["id"] for row in validated["visual_contexts"]}
        if visual_ids != self.valid_ids:
            missing = sorted(self.valid_ids - visual_ids)
            raise ValueError(f"Không thể lưu: Visual Context thiếu ID transcript: {missing[:20]}")
        return validated

    def apply(self):
        logger.info("[CONTEXT SAVE] clicked candidate_status=ready")
        try:
            self.result_context = self.values()
            logger.info("[CONTEXT SAVE] validation=PASS visual_rows=%d result=accepted",
                        len(self.result_context.get("visual_contexts", [])))
            self.accept()
        except (ValueError, TypeError) as exc:
            logger.warning("[CONTEXT SAVE] validation=FAIL result=rejected reason=%s", exc)
            QMessageBox.warning(self, "Hồ sơ truyện", str(exc))
