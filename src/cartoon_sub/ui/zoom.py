from PySide6.QtCore import QObject, QEvent, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QTableView


class UIZoomManager(QObject):
    """Runtime-only UI scaling. Display 0% maps to a safe 50% actual scale."""

    changed = Signal(int)
    MINIMUM_SCALE = 0.50

    def __init__(self, application=None):
        super().__init__()
        self.application = application or QApplication.instance()
        self.percent = 100
        self.base_font = QFont(self.application.font())
        self.base_stylesheet = self.application.styleSheet()
        self._layout_bases = {}
        self._row_bases = {}

    @property
    def actual_scale(self):
        return self.MINIMUM_SCALE + (1.0 - self.MINIMUM_SCALE) * self.percent / 100.0

    def set_percent(self, value):
        value = max(0, min(100, int(value)))
        self.percent = value
        self.apply()
        self.changed.emit(value)

    def step(self, delta):
        self.set_percent(self.percent + int(delta))

    def apply(self):
        scale = self.actual_scale
        font = QFont(self.base_font)
        if font.pointSizeF() > 0:
            font.setPointSizeF(max(6.0, self.base_font.pointSizeF() * scale))
        elif font.pixelSize() > 0:
            font.setPixelSize(max(6, round(self.base_font.pixelSize() * scale)))
        self.application.setFont(font)

        button_v = max(1, round(4 * scale))
        button_h = max(2, round(8 * scale))
        control_h = max(14, round(22 * scale))
        tab_v = max(2, round(5 * scale))
        tab_h = max(4, round(10 * scale))
        row_h = max(12, round(24 * scale))
        scrollbar = max(8, round(14 * scale))
        zoom_style = f"""
        QAbstractButton {{ padding: {button_v}px {button_h}px; min-height: {control_h}px; }}
        QLineEdit, QComboBox, QAbstractSpinBox {{ min-height: {control_h}px; }}
        QTabBar::tab {{ padding: {tab_v}px {tab_h}px; }}
        QHeaderView::section {{ padding: {max(1, round(4 * scale))}px; }}
        QTreeView::item {{ min-height: {row_h}px; }}
        QScrollBar:vertical {{ width: {scrollbar}px; }}
        QScrollBar:horizontal {{ height: {scrollbar}px; }}
        """
        self.application.setStyleSheet(self.base_stylesheet + "\n" + zoom_style)

        for widget in self.application.allWidgets():
            layout = widget.layout()
            if layout is not None:
                key = id(layout)
                if key not in self._layout_bases:
                    margins = layout.contentsMargins()
                    self._layout_bases[key] = (
                        layout, (margins.left(), margins.top(), margins.right(), margins.bottom()),
                        layout.spacing(),
                    )
                _, margins, spacing = self._layout_bases[key]
                layout.setContentsMargins(*(max(0, round(value * scale)) for value in margins))
                if spacing >= 0:
                    layout.setSpacing(max(0, round(spacing * scale)))
            if isinstance(widget, QTableView):
                header = widget.verticalHeader()
                key = id(header)
                if key not in self._row_bases:
                    self._row_bases[key] = (header, header.defaultSectionSize())
                _, base_size = self._row_bases[key]
                header.setDefaultSectionSize(max(12, round(base_size * scale)))
            widget.updateGeometry()
        self.application.sendEvent(self.application, QEvent(QEvent.Type.LayoutRequest))
