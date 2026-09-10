from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QTableWidget, QAbstractItemView, QHeaderView

def page(description):
    widget = QWidget()
    layout = QVBoxLayout(widget)
    label = QLabel(description)
    label.setWordWrap(True)
    layout.addWidget(label)
    return widget, layout

def table(headers):
    widget = QTableWidget(0, len(headers))
    widget.setHorizontalHeaderLabels(headers)
    widget.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    widget.setWordWrap(True);widget.setTextElideMode(Qt.TextElideMode.ElideNone)
    widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
    widget.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
    widget.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    widget.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    widget.horizontalHeader().sectionResized.connect(lambda *_:widget.resizeRowsToContents())
    widget.setColumnWidth(len(headers)-1,520)
    return widget
