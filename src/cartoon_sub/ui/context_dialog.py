from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPlainTextEdit, QTabWidget,
    QWidget, QFormLayout, QTableWidget, QTableWidgetItem, QPushButton, QMessageBox, QHeaderView)
from cartoon_sub.translation.context_models import StoryContext
import json


class ContextDialog(QDialog):
    """The model's proposal never silently replaces the applied context."""
    def __init__(self, context, valid_ids, parent=None, proposal=False):
        super().__init__(parent)
        self.valid_ids = valid_ids
        self.result_context = None
        self.setWindowTitle("Duyệt & lưu ngữ cảnh AI" if proposal else "Ngữ cảnh AI đã duyệt")
        self.resize(1050, 720)
        layout = QVBoxLayout(self)
        note = QLabel("Kiểm tra và sửa tên, quan hệ, thuật ngữ, xưng hô cùng mọi suy luận của AI. Để trống điều chưa rõ; "
                      "ID bằng chứng là số dòng Transcript (ngăn bằng dấu phẩy). Bản được lưu sẽ là source-of-truth cho các lần dịch sau.")
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
                  ["Character ID", "Tên", "Vai trò", "Gender context", "Quan hệ", "Mô tả hình ảnh", "SPK liên kết", "Confidence", "ID bằng chứng"]),
                 ("speaker_character_mappings", "SPK → nhân vật", ["spk_id", "character_id", "confidence", "evidence_ids", "notes"],
                  ["SPK", "Character ID", "Confidence", "ID bằng chứng", "Ghi chú"])]
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
                    value = row[name]
                    table.setItem(index, col, QTableWidgetItem(", ".join(map(str, value)) if isinstance(value, list) else value))
            box.addWidget(table)
            buttons = QHBoxLayout()
            add, remove = QPushButton("Thêm dòng"), QPushButton("Xóa dòng chọn")
            add.clicked.connect(lambda checked=False, t=table: t.insertRow(t.rowCount()))
            remove.clicked.connect(lambda checked=False, t=table: t.removeRow(t.currentRow()) if t.currentRow() >= 0 else None)
            buttons.addWidget(add)
            buttons.addWidget(remove)
            box.addLayout(buttons)
            list_fields = {"relationships", "associated_speakers"}
            self.tables[key] = (table, keys, list_fields)
            tabs.addTab(page, title)
        visual_page = QWidget()
        visual_layout = QVBoxLayout(visual_page)
        visual_help = QLabel("Visual context theo từng ID. Có thể sửa speaker/addressee/referent/visible character, scene mode, confidence và status dưới dạng JSON. UNKNOWN được ưu tiên khi chưa chắc.")
        visual_help.setWordWrap(True)
        self.visual_json = QPlainTextEdit()
        self.visual_json.setPlainText(json.dumps(context.get("visual_contexts", []), ensure_ascii=False, indent=2))
        visual_layout.addWidget(visual_help)
        visual_layout.addWidget(self.visual_json)
        tabs.addTab(visual_page, "Visual theo ID")
        buttons = QHBoxLayout()
        cancel, apply = QPushButton("Đóng, chưa lưu"), QPushButton("Lưu ngữ cảnh đã duyệt")
        cancel.clicked.connect(self.reject)
        apply.clicked.connect(self.apply)
        buttons.addWidget(cancel)
        buttons.addWidget(apply)
        layout.addLayout(buttons)

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
                if "confidence" in row:
                    try:
                        row["confidence"] = float(row["confidence"])
                    except ValueError:
                        raise ValueError(f"Confidence ở dòng {index + 1} phải là số 0–1") from None
                rows.append(row)
            data[key] = rows
        try:
            data["visual_contexts"] = json.loads(self.visual_json.toPlainText() or "[]")
        except ValueError:
            raise ValueError("Visual context JSON không hợp lệ") from None
        return StoryContext.from_dict(data, self.valid_ids).to_dict()

    def apply(self):
        try:
            self.result_context = self.values()
            self.accept()
        except (ValueError, TypeError) as exc:
            QMessageBox.warning(self, "Hồ sơ truyện", str(exc))
