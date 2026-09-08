from PySide6.QtWidgets import QTableWidget,QAbstractItemView,QTableWidgetItem
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from cartoon_sub.translation.qc import review_translation
from cartoon_sub.translation.modes import MODE_LABELS

def create_table():
    table=QTableWidget(0,14)
    table.setHorizontalHeaderLabels(["ID","Start","End","Duration","Speaker","Chinese","ZH Syl","VI Subtitle","VI Dubbing","VI Syl","Target","Δ target","Mode","QC"])
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setWordWrap(False)
    table.setColumnWidth(5,220); table.setColumnWidth(7,240); table.setColumnWidth(8,240); table.setColumnWidth(13,300)
    return table

def selected_ids(table):
    return [table.item(index.row(),0).data(Qt.ItemDataRole.UserRole) for index in table.selectionModel().selectedRows()]

def populate(table,project,view="both"):
    warnings=review_translation(project)
    table.setRowCount(len(project.segments))
    for row,s in enumerate(project.segments):
        actual=s.vi_subtitle_syllables if view=="subtitle" else s.vi_syllables
        delta=actual-s.target_syllables
        values=[s.id,f"{s.start:.3f}",f"{s.end:.3f}",f"{s.duration:.3f}",f"{s.speaker_id} · {s.speaker_name}",s.zh,s.zh_syllables,s.vi_subtitle,s.vi_dubbing,actual,s.target_syllables,f"{delta:+d}",MODE_LABELS[s.translation_mode]," | ".join(warnings[str(s.id)]) or "Không cảnh báo tự động"]
        for col,value in enumerate(values):
            item=QTableWidgetItem(str(value)); item.setToolTip(str(value)); item.setData(Qt.ItemDataRole.UserRole,s.id)
            if col in (9,10,11):
                color="#d7f2da" if delta==0 else "#fff0be" if abs(delta)<=max(1,int(s.target_syllables*.15)) else "#ffd0d0"
                if s.translation_mode=="strict_iso_syllabic" and delta: color="#ffd0d0"
                item.setBackground(QColor(color)); item.setForeground(QColor("#171717"))
            table.setItem(row,col,item)
    table.setColumnHidden(7,view=="dubbing"); table.setColumnHidden(8,view=="subtitle")
