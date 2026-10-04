from PySide6.QtWidgets import QFormLayout, QGroupBox, QLabel


class CacheStatusBox(QGroupBox):
    def __init__(self, parent=None):
        super().__init__("Trạng thái dữ liệu / cache", parent)
        self._layout = QFormLayout(self)
        self._labels = {}
        self.setToolTip("CACHED = hợp lệ; PARTIAL = một phần; STALE = dữ liệu cũ; NONE = chưa có")

    def set_statuses(self, statuses):
        for name, value in statuses.items():
            label = self._labels.get(name)
            if label is None:
                label = QLabel()
                self._labels[name] = label
                self._layout.addRow(name, label)
            label.setText(value or "NONE")
