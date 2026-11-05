"""Ctrl+G: jump to a file in the current folder by position number or part of its name."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Sequence

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout, QWidget

MAX_RESULTS = 200


def match_files(files: Sequence[Path], query: str) -> List[int]:
    """Indices of files matching the query, best first.

    A number matches that 1-based position; any text matches names containing it
    (case-insensitive), with names that start with it listed first. Empty query = all files.
    """
    text = query.strip().casefold()
    if not text:
        return list(range(len(files)))
    hits: List[int] = []
    if text.isdigit() and 1 <= int(text) <= len(files):
        hits.append(int(text) - 1)
    names = [p.name.casefold() for p in files]
    starts = [i for i, n in enumerate(names) if n.startswith(text) and i not in hits]
    contains = [i for i, n in enumerate(names) if text in n and not n.startswith(text) and i not in hits]
    return hits + starts + contains


class GoToFileDialog(QDialog):
    """Type to filter, ↑/↓ to choose, Enter to open."""

    def __init__(self, files: Sequence[Path], current_index: int, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("gotoDialog")  # styled by theme.app_qss()
        self.setWindowTitle("Go to File — HakuView")
        self.setMinimumSize(460, 380)
        self._files = list(files)
        self._current = current_index
        self.selected_index: Optional[int] = None

        layout = QVBoxLayout(self)
        self.edit = QLineEdit(self)
        self.edit.setPlaceholderText(f"Number (1–{len(self._files)}) or part of a file name")
        self.edit.setClearButtonEnabled(True)
        self.list = QListWidget(self)
        self.list.setUniformItemSizes(True)
        self.lbl_count = QLabel(self)
        layout.addWidget(self.edit)
        layout.addWidget(self.list, 1)
        layout.addWidget(self.lbl_count)

        self.edit.textChanged.connect(self._refilter)
        self.edit.returnPressed.connect(self.accept)
        self.list.itemActivated.connect(lambda _item: self.accept())
        self.edit.installEventFilter(self)
        self._refilter("")

    def _refilter(self, query: str) -> None:
        matches = match_files(self._files, query)
        self.list.clear()
        width = len(str(len(self._files)))
        for i in matches[:MAX_RESULTS]:
            item = QListWidgetItem(f"{i + 1:>{width}}   {self._files[i].name}")
            item.setData(Qt.ItemDataRole.UserRole, i)
            self.list.addItem(item)
        if query.strip():
            self.list.setCurrentRow(0)
        elif self.list.count():
            self.list.setCurrentRow(min(max(self._current, 0), self.list.count() - 1))
        shown = min(len(matches), MAX_RESULTS)
        more = f" (showing first {shown})" if len(matches) > shown else ""
        self.lbl_count.setText(f"{len(matches)} of {len(self._files)} files{more}" if matches else "No matching files")

    def eventFilter(self, obj, event) -> bool:
        # Arrow keys in the search box move the list selection, so typing and choosing don't need the mouse.
        if obj is self.edit and isinstance(event, QKeyEvent) and event.type() == QKeyEvent.Type.KeyPress:
            if event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown):
                self.list.keyPressEvent(event)
                return True
        return super().eventFilter(obj, event)

    def accept(self) -> None:
        item = self.list.currentItem()
        if item is None:
            return
        self.selected_index = item.data(Qt.ItemDataRole.UserRole)
        super().accept()
