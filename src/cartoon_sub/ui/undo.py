"""One reusable Qt undo mechanism for committed UI and project-state edits."""
from PySide6.QtGui import QUndoCommand


class ValueCommand(QUndoCommand):
    def __init__(self, text, old_value, new_value, apply):
        super().__init__(text)
        self.old_value, self.new_value, self.apply = old_value, new_value, apply

    def undo(self):
        self.apply(self.old_value)

    def redo(self):
        self.apply(self.new_value)


class AppliedValueCommand(ValueCommand):
    """Command for an edit already reflected by the widget/project before push."""
    def __init__(self, text, old_value, new_value, apply):
        super().__init__(text, old_value, new_value, apply)
        self._first_redo = True

    def redo(self):
        if self._first_redo:
            self._first_redo = False
            return
        super().redo()
