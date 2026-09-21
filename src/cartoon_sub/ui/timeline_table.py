from PySide6.QtWidgets import QTableWidget,QAbstractItemView,QTableWidgetItem,QHeaderView
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from cartoon_sub.translation.qc import review_translation
from cartoon_sub.translation.modes import MODE_LABELS

EDITABLE_COLUMNS = {1, 2, 4, 5, 7, 8}  # Start, End, Speaker, Chinese, VI Subtitle, VI Dubbing


def create_table():
    table = QTableWidget(0, 14)
    table.setHorizontalHeaderLabels([
        "ID", "Start", "End", "Duration", "Speaker", "Chinese", "ZH Syl",
        "VI Subtitle", "VI Dubbing", "VI Syl", "Target", "Δ target", "Mode", "QC"
    ])
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    table.setEditTriggers(
        QAbstractItemView.EditTrigger.DoubleClicked
        | QAbstractItemView.EditTrigger.SelectedClicked
        | QAbstractItemView.EditTrigger.EditKeyPressed
        | QAbstractItemView.EditTrigger.AnyKeyPressed
    )
    table.setWordWrap(True)
    table.setTextElideMode(Qt.TextElideMode.ElideNone)
    table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
    table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    table.horizontalHeader().sectionResized.connect(lambda *_: table.resizeRowsToContents())
    table.setColumnWidth(5, 220)
    table.setColumnWidth(7, 240)
    table.setColumnWidth(8, 240)
    table.setColumnWidth(13, 300)

    table.dirty_rows = set()
    table.original_values = {}
    table.is_populating = False
    table.on_dirty_changed = None

    def on_item_changed(item):
        if getattr(table, "is_populating", False):
            return
        col = item.column()
        if col not in EDITABLE_COLUMNS:
            return
        row = item.row()
        id_item = table.item(row, 0)
        if not id_item:
            return
        uid = id_item.data(Qt.ItemDataRole.UserRole)
        orig = table.original_values.get((uid, col), "")
        curr = item.text().strip()
        if curr != orig:
            table.dirty_rows.add(uid)
            item.setBackground(QColor("#fff8dc"))
        else:
            is_row_dirty = any(
                table.item(row, c) is not None
                and table.item(row, c).text().strip() != table.original_values.get((uid, c), "")
                for c in EDITABLE_COLUMNS
            )
            if not is_row_dirty:
                table.dirty_rows.discard(uid)
            item.setBackground(QColor("transparent"))

        if callable(table.on_dirty_changed):
            table.on_dirty_changed(len(table.dirty_rows))

    table.itemChanged.connect(on_item_changed)
    return table

def selected_ids(table):
    return [table.item(index.row(), 0).data(Qt.ItemDataRole.UserRole) for index in table.selectionModel().selectedRows()]

def get_dirty_rows(table):
    results = []
    if not getattr(table, "dirty_rows", None):
        return results
    for row in range(table.rowCount()):
        id_item = table.item(row, 0)
        if not id_item:
            continue
        uid = id_item.data(Qt.ItemDataRole.UserRole)
        if uid in table.dirty_rows:
            results.append({
                "id": uid,
                "row_index": row + 1,
                "start": table.item(row, 1).text().strip() if table.item(row, 1) else "",
                "end": table.item(row, 2).text().strip() if table.item(row, 2) else "",
                "speaker": table.item(row, 4).text().strip() if table.item(row, 4) else "",
                "zh": table.item(row, 5).text().strip() if table.item(row, 5) else "",
                "vi_subtitle": table.item(row, 7).text().strip() if table.item(row, 7) else "",
                "vi_dubbing": table.item(row, 8).text().strip() if table.item(row, 8) else "",
            })
    return results

def populate(table, project, view="both"):
    table.is_populating = True
    table.dirty_rows.clear()
    table.original_values.clear()
    warnings = review_translation(project)
    table.setRowCount(len(project.segments))
    for row, s in enumerate(project.segments):
        actual = s.vi_subtitle_syllables if view == "subtitle" else s.vi_syllables
        delta = actual - s.target_syllables
        values = [
            s.id,
            f"{s.start:.3f}",
            f"{s.end:.3f}",
            f"{s.duration:.3f}",
            f"{s.speaker_id} · {s.speaker_name}",
            s.zh,
            s.zh_syllables,
            s.vi_subtitle,
            s.vi_dubbing,
            actual,
            s.target_syllables,
            f"{delta:+d}",
            MODE_LABELS[s.translation_mode],
            " | ".join(warnings[str(s.id)]) or "Không cảnh báo tự động"
        ]
        for col, value in enumerate(values):
            item = QTableWidgetItem(str(value))
            item.setToolTip(str(value))
            item.setData(Qt.ItemDataRole.UserRole, s.id)
            if col not in EDITABLE_COLUMNS:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            else:
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
                table.original_values[(s.id, col)] = str(value)
            if col in (9, 10, 11):
                color = "#d7f2da" if delta == 0 else "#fff0be" if abs(delta) <= max(1, int(s.target_syllables * .15)) else "#ffd0d0"
                if s.translation_mode == "strict_iso_syllabic" and delta:
                    color = "#ffd0d0"
                item.setBackground(QColor(color))
                item.setForeground(QColor("#171717"))
            table.setItem(row, col, item)
    table.setColumnHidden(7, view == "dubbing")
    table.setColumnHidden(8, view == "subtitle")
    table.resizeRowsToContents()
    table.is_populating = False
    if callable(table.on_dirty_changed):
        table.on_dirty_changed(0)
