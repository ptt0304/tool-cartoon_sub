"""Compact reusable search binding for model combo boxes."""
from PySide6.QtCore import QObject, Signal

from cartoon_sub.ai.openrouter_client import model_author


class SearchableModelBinding(QObject):
    """Filter a model combo locally without changing its persisted selection."""

    explicitSelectionChanged = Signal(str)

    def __init__(self, search, combo, parent=None):
        super().__init__(parent)
        self.search = search
        self.combo = combo
        self.catalog = []
        self.selected_id = ""
        self.default_label = "Mặc định — chưa chọn"
        self.search.textChanged.connect(self.rebuild)
        self.combo.activated.connect(self._activated)

    def set_models(self, catalog, *, selected_id="", default_label=None):
        self.catalog = list(catalog or [])
        self.selected_id = str(selected_id or "")
        if default_label is not None:
            self.default_label = default_label
        self.rebuild()

    def rebuild(self, *_args):
        query = self.search.text().strip().casefold()
        matches = []
        for model in self.catalog:
            haystack = " ".join((
                str(model.get("id", "")),
                str(model.get("name") or model.get("display_name") or ""),
                model_author(model),
            )).casefold()
            if not query or query in haystack:
                matches.append(model)

        self.combo.blockSignals(True)
        self.combo.clear()
        self.combo.addItem(self.default_label, "")
        for model in matches:
            model_id = model.get("id", "")
            if model_id:
                self.combo.addItem(
                    f"{model.get('name') or model.get('display_name') or model_id} — {model_id}",
                    model_id,
                )
        index = self.combo.findData(self.selected_id)
        if index < 0 and self.selected_id:
            # Match Settings > AI behavior: retain an explicit selection while
            # it is temporarily hidden by the local search filter.
            model = next((item for item in self.catalog
                          if item.get("id") == self.selected_id), None)
            if model:
                label = model.get("name") or model.get("display_name") or self.selected_id
                self.combo.insertItem(0, f"[Ẩn bởi tìm kiếm] {label} — {self.selected_id}",
                                      self.selected_id)
                index = 0
        self.combo.setCurrentIndex(index if index >= 0 else 0)
        self.combo.blockSignals(False)

    def _activated(self, _index):
        self.selected_id = self.combo.currentData() or ""
        self.explicitSelectionChanged.emit(self.selected_id)
