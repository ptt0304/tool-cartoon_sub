"""Keep page scrolling from silently changing input values."""
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QPointF
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import (
    QAbstractScrollArea, QAbstractSpinBox, QComboBox, QSlider,
)


def _forward_wheel_to_scroll_area(watched, event):
    """Forward a wheel gesture to the containing page, if it has one."""
    parent = watched.parentWidget()
    while parent is not None and not isinstance(parent, QAbstractScrollArea):
        parent = parent.parentWidget()
    if parent is None:
        event.ignore()
        return
    position = watched.mapTo(parent.viewport(), event.position().toPoint())
    forwarded = QWheelEvent(
        QPointF(position), event.globalPosition(), event.pixelDelta(), event.angleDelta(),
        event.buttons(), event.modifiers(), event.phase(), event.inverted(), event.source(),
    )
    QCoreApplication.sendEvent(parent.viewport(), forwarded)
    event.accept()


class NoWheelComboBox(QComboBox):
    """Keep a closed combo stable while forwarding page-scroll gestures."""

    def wheelEvent(self, event):
        if self.view().isVisible():
            super().wheelEvent(event)
            return
        _forward_wheel_to_scroll_area(self, event)


class GlobalWheelGuard(QObject):
    """Apply the application-wide wheel policy to selection and numeric inputs."""

    def eventFilter(self, watched, event):
        if isinstance(watched, QComboBox) and event.type() == QEvent.Type.Wheel:
            if watched.view().isVisible():
                return super().eventFilter(watched, event)
            _forward_wheel_to_scroll_area(watched, event)
            return True
        if (isinstance(watched, (QAbstractSpinBox, QSlider))
                and event.type() == QEvent.Type.Wheel):
            _forward_wheel_to_scroll_area(watched, event)
            return True
        return super().eventFilter(watched, event)


# Compatibility name retained for focused tests and any external UI imports.
NoWheelNumericFilter = GlobalWheelGuard
