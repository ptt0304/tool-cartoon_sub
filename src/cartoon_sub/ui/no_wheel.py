"""Keep page scrolling from silently changing numeric settings."""
from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QAbstractScrollArea, QAbstractSpinBox


class NoWheelNumericFilter(QObject):
    def eventFilter(self, watched, event):
        if isinstance(watched, QAbstractSpinBox) and event.type() == QEvent.Type.Wheel:
            parent = watched.parentWidget()
            while parent is not None and not isinstance(parent, QAbstractScrollArea):
                parent = parent.parentWidget()
            if isinstance(parent, QAbstractScrollArea):
                bar = parent.verticalScrollBar()
                delta = event.angleDelta().y()
                bar.setValue(bar.value() - (bar.singleStep() * 3 if delta > 0 else -bar.singleStep() * 3))
            event.ignore()
            return True
        return super().eventFilter(watched, event)
