"""Non-destructive Unicode search helpers for timeline tables and trees."""

import unicodedata

from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton


def _needle(value):
    return unicodedata.normalize("NFKC", str(value or "")).casefold().strip()


def add_table_search(layout, table, columns):
    row = QHBoxLayout()
    edit = QLineEdit()
    edit.setPlaceholderText("Chinese, Vietnamese, speaker…")
    clear = QPushButton("X")
    clear.setMaximumWidth(34)
    row.addWidget(QLabel("Tìm kiếm:"))
    row.addWidget(edit, 1)
    row.addWidget(clear)
    layout.addLayout(row)
    table.search_edit = edit
    table.search_row = row
    table.search_columns = tuple(columns)
    edit.textChanged.connect(lambda text: apply_table_search(table, text, table.search_columns))
    clear.clicked.connect(edit.clear)
    return edit, clear


def apply_table_search(table, query=None, columns=None):
    query = _needle(query if query is not None else getattr(table, "search_edit", None).text())
    columns = tuple(columns if columns is not None else getattr(table, "search_columns", ()))
    for row in range(table.rowCount()):
        matches = not query or any(
            query in _needle(table.item(row, column).text())
            for column in columns if table.item(row, column) is not None
        )
        predicate = getattr(table, "row_filter_predicate", None)
        if matches and callable(predicate):
            matches = bool(predicate(row))
        table.setRowHidden(row, not matches)


def add_tree_search(layout, tree, parent_columns, child_columns):
    row = QHBoxLayout()
    edit = QLineEdit()
    edit.setPlaceholderText("Vietnamese, speaker…")
    clear = QPushButton("X")
    clear.setMaximumWidth(34)
    row.addWidget(QLabel("Tìm kiếm:"))
    row.addWidget(edit, 1)
    row.addWidget(clear)
    layout.addLayout(row)
    tree.search_edit = edit
    tree.search_parent_columns = tuple(parent_columns)
    tree.search_child_columns = tuple(child_columns)
    edit.textChanged.connect(lambda text: apply_tree_search(tree, text))
    clear.clicked.connect(edit.clear)
    return edit, clear


def apply_tree_search(tree, query=None):
    query = _needle(query if query is not None else tree.search_edit.text())
    for row in range(tree.topLevelItemCount()):
        parent = tree.topLevelItem(row)
        parent_match = not query or any(query in _needle(parent.text(column))
                                        for column in tree.search_parent_columns)
        child_match = False
        for index in range(parent.childCount()):
            child = parent.child(index)
            matches = parent_match or any(query in _needle(child.text(column))
                                          for column in tree.search_child_columns)
            child.setHidden(not matches)
            child_match = child_match or matches
        parent.setHidden(not (parent_match or child_match))
        if query and child_match:
            parent.setExpanded(True)
