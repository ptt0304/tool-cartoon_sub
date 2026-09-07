from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QTableWidget, QAbstractItemView

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
    widget.horizontalHeader().setStretchLastSection(True)
    return widget
