"""Run the attendance timeline demo."""
from __future__ import annotations

import sys
import re
from PyQt6.QtCore import QEvent, QItemSelectionModel, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QKeySequence, QPen, QRegularExpressionValidator, QShortcut
from PyQt6.QtCore import QRegularExpression
from PyQt6.QtWidgets import QApplication, QAbstractItemView, QComboBox, QGraphicsRectItem, QGraphicsScene, QGraphicsTextItem, QGraphicsView, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu, QPushButton, QSizePolicy, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget

from dochazka.timeline import BlockKind, KINDS, TimelineController

SCALE, LEFT, TOP = 1.05, 50, 55
EDGE_GRAB_WIDTH = 7
SNAP_MINUTES = 8

def clock(minute: int) -> str: return f"{minute // 60:02}:{minute % 60:02}"
def pointer_to_minute(x: float) -> int: return max(0, min(1440, round((x - LEFT) / SCALE)))
def parse_clock(value: str) -> int | None:
    try:
        hour, minute = (int(part) for part in value.strip().split(":"))
        return hour * 60 + minute if 0 <= hour < 24 and 0 <= minute < 60 or (hour, minute) == (24, 0) else None
    except ValueError:
        return None

class TimeEdit(QLineEdit):
    """A forgiving HH:MM field that helps while the user is still typing."""
    timeDragged = pyqtSignal()
    advanceRequested = pyqtSignal()
    escapeRequested = pyqtSignal()
    def __init__(self, value: str = ""):
        super().__init__(value)
        self.setValidator(QRegularExpressionValidator(QRegularExpression(r"\d{0,2}:?\d{0,2}"), self))
        self.textEdited.connect(self._assist)
        self.editingFinished.connect(self._finish)
        self._drag_origin_x: float | None = None
        self._drag_origin_minute = 720
        self._drag_button: Qt.MouseButton | None = None
        self._time_dragging = False
        self._drag_fallback = None
        self._active_component: int | None = None
        self._pending_component: int | None = None
        self.setCursor(Qt.CursorShape.SizeHorCursor)

    def _set_text(self, value: str):
        self.blockSignals(True); self.setText(value); self.setCursorPosition(len(value)); self.blockSignals(False)

    def _assist(self, value: str):
        value = value.strip()
        if re.fullmatch(r"[3-9]", value):
            self._set_text(f"0{value}:")
        elif re.fullmatch(r"\d{2}", value):
            self._set_text(f"{value}:")
        elif re.fullmatch(r"\d:", value):
            self._set_text(f"0{value}")

    def _finish(self):
        match = re.fullmatch(r"(\d{1,2}):(\d{1,2})", self.text().strip())
        if match:
            hour, minute = (int(part) for part in match.groups())
            if parse_clock(f"{hour:02}:{minute:02}") is not None:
                self._set_text(f"{hour:02}:{minute:02}")
        self._pending_component = None

    def _component_at_cursor(self, cursor: int) -> int | None:
        if not re.fullmatch(r"\d{2}:\d{2}", self.text()):
            return None
        return 0 if cursor <= 2 else 3

    def _select_component(self, component: int):
        self._active_component = component
        self._pending_component = None
        self.setSelection(component, 2)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        if re.fullmatch(r"\d{2}:\d{2}", self.text()):
            self._select_component(0)

    def keyPressEvent(self, event):
        text = self.text()
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._finish()
            self.editingFinished.emit()
            self.advanceRequested.emit()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape:
            self.escapeRequested.emit()
            event.accept()
            return
        component = self._active_component if self._active_component is not None else self._component_at_cursor(self.cursorPosition())
        digit = event.text() if event.text().isdigit() and len(event.text()) == 1 else None
        if digit is not None and component is not None and re.fullmatch(r"\d{2}:\d{2}", text):
            next_component = component
            if self._pending_component == component:
                pair = text[component + 1] + digit
                self._pending_component = None
            else:
                pair = "0" + digit
                self._pending_component = component
            if component == 0:
                if self._pending_component == component and int(digit) >= 3:
                    # 03, 04 … are complete valid hours: continue at minutes.
                    self._pending_component = None
                    next_component = 3
                elif self._pending_component is None:
                    pair = f"{min(int(pair), 23):02}"
                    next_component = 3
            updated = text[:component] + pair + text[component + 2:]
            self._set_text(updated)
            self._active_component = next_component
            self.setSelection(next_component, 2)
            event.accept()
            return
        if event.key() in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete) and ":" in text:
            colon = text.index(":")
            cursor = self.cursorPosition()
            # Backspace at the separator removes the preceding digit, never
            # the separator itself. Keep the field structurally editable.
            if event.key() == Qt.Key.Key_Backspace and cursor in (colon, colon + 1):
                remove = colon - 1
                self._set_text(text[:remove] + text[remove + 1:])
                self.setCursorPosition(remove)
                event.accept()
                return
            if event.key() == Qt.Key.Key_Delete and cursor == colon:
                remove = colon + 1
                self._set_text(text[:remove] + text[remove + 1:])
                self.setCursorPosition(colon)
                event.accept()
                return
        if event.text() == ":" and ":" in text:
            event.accept()
            return
        self._pending_component = None
        super().keyPressEvent(event)

    def set_drag_fallback(self, fallback):
        """Set a callable used when this otherwise-empty field is dragged."""
        self._drag_fallback = fallback

    @staticmethod
    def dragged_minute(origin: int, pixels: float, fine: bool) -> int:
        delta = int(pixels / 10) if fine else round(pixels)
        return max(0, min(1440, origin + delta))

    def mousePressEvent(self, event):
        if event.button() not in (Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton):
            super().mousePressEvent(event); return
        self._drag_origin_x = event.position().x()
        parsed = parse_clock(self.text())
        fallback = self._drag_fallback() if self._drag_fallback is not None else None
        self._drag_origin_minute = parsed if parsed is not None else (fallback if fallback is not None else 720)
        self._drag_button = event.button()
        self._time_dragging = False
        if event.button() == Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
        else:
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_origin_x is None:
            super().mouseMoveEvent(event); return
        if abs(event.position().x() - self._drag_origin_x) >= 3:
            self._time_dragging = True
        if self._time_dragging:
            minute = self.dragged_minute(self._drag_origin_minute, event.position().x() - self._drag_origin_x, self._drag_button == Qt.MouseButton.RightButton)
            # Update only the field while dragging. The connected table/model
            # transition is intentionally emitted once, on mouse release.
            self._set_text(clock(minute))
        event.accept()

    def mouseReleaseEvent(self, event):
        if self._drag_origin_x is None or event.button() != self._drag_button:
            super().mouseReleaseEvent(event); return
        if self._time_dragging:
            self.timeDragged.emit()
            event.accept()
        else:
            super().mouseReleaseEvent(event)
            component = self._component_at_cursor(self.cursorPosition())
            if component is not None:
                self._select_component(component)
        self._drag_origin_x = None
        self._drag_button = None

class TypeCombo(QComboBox):
    """Combo box that behaves like an editable table field on Enter/Escape."""
    advanceRequested = pyqtSignal()
    escapeRequested = pyqtSignal()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down) and not self.view().isVisible():
            event.accept()
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if not self.view().isVisible():
                self.showPopup()
            else:
                self.hidePopup()
                self.activated.emit(self.currentIndex())
                self.advanceRequested.emit()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape:
            self.hidePopup()
            self.escapeRequested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

class TimelineView(QGraphicsView):
    """A single-row timeline reads naturally with a horizontal mouse wheel."""
    def wheelEvent(self, event):
        delta = event.angleDelta().y() or event.angleDelta().x()
        self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta)
        event.accept()

class SegmentItem(QGraphicsRectItem):
    def __init__(self, segment, window: "TimelineWindow"):
        width = (segment.end_minute - segment.start_minute) * SCALE
        super().__init__(0, 0, width, 54)
        self.segment, self.window = segment, window
        self._drag_start: int | None = None
        self._dragged = False
        self._fine_drag = False
        self._drag_mode = "move"
        self._resize_anchor = 0
        self._gesture_key = segment.key
        self._gesture_segment = segment
        self._snap_segments = ()
        self.setBrush(QColor(KINDS[segment.kind].color)); self.setFlag(QGraphicsRectItem.GraphicsItemFlag.ItemIsSelectable); self.setAcceptHoverEvents(True)
        text = QGraphicsTextItem(self)
        text.setDefaultTextColor(QColor("white")); text.setPos(4, 3); text.setTextWidth(max(1, width - 8)); text.setZValue(1)
        self.label = text
        self.set_segment_geometry(segment)

    def set_segment_geometry(self, segment):
        """Keep the label inside its rectangle; hide it rather than clipping."""
        self.segment = segment
        width = (segment.end_minute - segment.start_minute) * SCALE
        self.setRect(0, 0, width, 54)
        self.setPos(LEFT + segment.start_minute * SCALE, TOP)
        label_text = f"{KINDS[segment.kind].label}\n{clock(segment.start_minute)} – {clock(segment.end_minute)}"
        self.label.setPlainText(label_text)
        self.label.setTextWidth(-1)
        required_width = self.label.document().idealWidth()
        fits = width - 8 >= required_width
        self.label.setVisible(fits)
        if fits:
            self.label.setTextWidth(required_width)
        self.setToolTip(f"{KINDS[segment.kind].label}: {clock(segment.start_minute)} – {clock(segment.end_minute)} ({segment.end_minute - segment.start_minute} min)" if fits else "")
    def mousePressEvent(self, event):
        if event.button() not in (Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton):
            event.ignore(); return
        modifiers = event.modifiers()
        if event.button() == Qt.MouseButton.RightButton:
            # Preserve a group only when the context target already belongs to it.
            if self.segment.source_id not in self.window.controller.state.selected:
                self.window.controller.select(self.segment.source_id)
        elif self.segment.source_id not in self.window.controller.state.selected or modifiers & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier):
            self.window.controller.select(self.segment.source_id,
                                          toggle=bool(modifiers & Qt.KeyboardModifier.ControlModifier),
                                          range_select=bool(modifiers & Qt.KeyboardModifier.ShiftModifier))
        self.window.view.setFocus()
        self._drag_start = pointer_to_minute(event.scenePos().x())
        self._dragged = False
        self._fine_drag = event.button() == Qt.MouseButton.RightButton
        self._gesture_segment = self.segment
        self._snap_segments = self.window.controller.projection.segments
        if event.pos().x() <= EDGE_GRAB_WIDTH:
            self._drag_mode = "resize-left"
            self._resize_anchor = self.segment.start_minute
        elif event.pos().x() >= self.rect().width() - EDGE_GRAB_WIDTH:
            self._drag_mode = "resize-right"
            self._resize_anchor = self.segment.end_minute
        else:
            self._drag_mode = "move"
        self.window.controller.begin_gesture()
        event.accept()

    def hoverMoveEvent(self, event):
        on_edge = event.pos().x() <= EDGE_GRAB_WIDTH or event.pos().x() >= self.rect().width() - EDGE_GRAB_WIDTH
        self.setCursor(Qt.CursorShape.SizeHorCursor if on_edge else Qt.CursorShape.OpenHandCursor)
        event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_start is None:
            event.ignore(); return
        raw_delta = pointer_to_minute(event.scenePos().x()) - self._drag_start
        self._dragged = self._dragged or raw_delta != 0
        if self._drag_mode.startswith("resize-"):
            adjusted = int(raw_delta / 5) if self._fine_drag else raw_delta
            self.window.preview_resize(self, self._drag_mode.removeprefix("resize-"), self._resize_anchor + adjusted)
        else:
            delta = int(raw_delta / 5) if self._fine_drag else raw_delta
            delta = self.snapped_delta(delta, event.modifiers())
            self.window.preview_drag(self, delta)
        event.accept()

    def snapped_delta(self, delta: int, modifiers) -> int:
        """Align the dragged block to a nearby unselected segment boundary."""
        if modifiers & (Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.ControlModifier):
            return delta
        selected = self.window.controller.state.selected
        start = self._gesture_segment.start_minute + delta
        end = self._gesture_segment.end_minute + delta
        boundaries = [0, 1440]
        for segment in self._snap_segments:
            if segment.source_id not in selected:
                boundaries.extend((segment.start_minute, segment.end_minute))
        corrections = [boundary - edge for edge in (start, end) for boundary in boundaries if abs(boundary - edge) <= SNAP_MINUTES]
        return delta + min(corrections, key=abs) if corrections else delta

    def mouseReleaseEvent(self, event):
        if self._drag_start is None:
            event.ignore(); return
        raw_delta = pointer_to_minute(event.scenePos().x()) - self._drag_start
        dragged = self._dragged or raw_delta != 0
        if dragged:
            self.window.controller.commit_gesture(self.segment.source_id)
        elif self._fine_drag:
            self.window.controller.cancel_gesture()
            menu = QMenu(); delete_action = menu.addAction("Delete block")
            menu.addSeparator()
            fill_left = menu.addAction("Fill gap left\tAlt+Left")
            fill_right = menu.addAction("Fill gap right\tAlt+Right")
            menu.addSeparator()
            for kind in BlockKind:
                action = menu.addAction(f"Change to {KINDS[kind].label}")
                action.setData(kind.value)
            chosen = menu.exec(event.screenPos())
            target_ids = self.window.controller.state.selected or frozenset({self.segment.source_id})
            if chosen is delete_action:
                self.window.controller.delete(target_ids)
            elif chosen is fill_left:
                self.window.controller.fill_selected_gaps(target_ids, "left")
            elif chosen is fill_right:
                self.window.controller.fill_selected_gaps(target_ids, "right")
            elif chosen is not None and chosen.data() in {kind.value for kind in BlockKind}:
                self.window.change_selected_kinds(target_ids, BlockKind(chosen.data()))
        else:
            self.window.controller.cancel_gesture()
        self._drag_start = None
        # Do not clear the graphics scene while Qt is still dispatching this
        # event to this item; schedule rebuilding after it has returned.
        self.window.defer_render()
        event.accept()

class TimelineWindow(QWidget):
    def __init__(self):
        super().__init__(); self.controller = TimelineController(); self.setWindowTitle("Docházka timeline")
        QApplication.instance().installEventFilter(self)
        self._handled_shortcut: tuple[int, Qt.KeyboardModifiers] | None = None
        self.setStyleSheet("""
            QWidget { background: #252b36; color: #e5e7eb; }
            QPushButton { background: #3a4a61; color: #edf2f7; border: 1px solid #566b86; border-radius: 4px; padding: 4px 7px; font-weight: 600; }
            QPushButton:hover { background: #465d79; }
            QPushButton:pressed { background: #304157; }
            QLineEdit, QComboBox { background: #313b4b; color: #f3f4f6; border: 1px solid #52647b; border-radius: 3px; padding: 3px; }
            QLineEdit:focus, QComboBox:focus { border: 1px solid #7399bd; }
            QGraphicsView { background: #1d2430; border: 2px solid #61738b; border-radius: 4px; }
            QTableWidget { background: #252b36; color: #e5e7eb; border: 1px solid #52647b; border-radius: 4px; gridline-color: #3b4657; }
            QTableWidget::item { background: #2d3747; color: #e5e7eb; }
            QTableWidget::item:selected { background: #435d78; }
            QHeaderView::section { background: #343f50; color: #edf2f7; border: none; border-bottom: 1px solid #52647b; padding: 5px; font-weight: 600; }
            QTableCornerButton::section { background: #343f50; border: none; }
        """)
        self.scene = QGraphicsScene(self); self.view = TimelineView(self.scene); self.view.setMinimumSize(720, 220); self._render_pending = False
        self._syncing_table = False; self._pending_rows: list[dict] = []; self._table_drafts: dict[str, tuple[str, str]] = {}
        layout = QVBoxLayout(self); body = QHBoxLayout(); timeline_panel = QVBoxLayout(); buttons = QHBoxLayout()
        for kind in BlockKind:
            button = QPushButton(f"Insert {KINDS[kind].label}"); button.clicked.connect(lambda _, k=kind: self.command(lambda: self.controller.add(k))); buttons.addWidget(button)
        for label, action in [("Close gaps", self.controller.close_selected_gaps), ("Undo", self.controller.undo), ("Redo", self.controller.redo), ("Reset example", self.reset_all)]:
            b = QPushButton(label); b.clicked.connect(lambda _, f=action: self.command(f)); buttons.addWidget(b)
        timeline_panel.addLayout(buttons); timeline_panel.addWidget(self.view); self.status = QLabel(); timeline_panel.addWidget(self.status)
        body.addLayout(timeline_panel, 1)
        table_container = QWidget(); table_container.setFixedWidth(380)
        table_panel = QVBoxLayout(table_container); table_panel.setContentsMargins(4, 4, 4, 4); table_panel.setSpacing(4); table_panel.addWidget(QLabel("Attendance rows (strict overwrite)"))
        add_buttons = QGridLayout()
        add_buttons.setHorizontalSpacing(4); add_buttons.setVerticalSpacing(4)
        for index, kind in enumerate(BlockKind):
            button = QPushButton(f"Add {KINDS[kind].label}")
            button.clicked.connect(lambda _, k=kind: self.add_table_row(k)); add_buttons.addWidget(button, index // 2, index % 2)
        table_panel.addLayout(add_buttons)
        self.table = QTableWidget(0, 3); self.table.setHorizontalHeaderLabels(["Type", "Start", "End"]); self.table.setMinimumWidth(0)
        self.table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().sectionClicked.connect(self.highlight_table_row)
        QShortcut(QKeySequence.StandardKey.Delete, self.table, self.delete_table_rows)
        for sequence, direction in (("Alt+Left", "left"), ("Alt+Right", "right")):
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(lambda d=direction: self.fill_selected_gap(d))
        table_panel.addWidget(self.table, 1)
        body.addWidget(table_container, 0); layout.addLayout(body); self.render()
    def command(self, action): action(); self.render()
    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress):
            ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
            shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            identity = (int(event.key()), event.modifiers())
            if event.type() == QEvent.Type.KeyPress and self._handled_shortcut == identity:
                self._handled_shortcut = None
                return True
            def handle(action):
                QTimer.singleShot(0, action)
                if event.type() == QEvent.Type.ShortcutOverride:
                    self._handled_shortcut = identity
                event.accept()
                return True
            if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
                focus = QApplication.focusWidget()
                in_table = ((isinstance(watched, QWidget) and (watched is self.table or self.table.isAncestorOf(watched)))
                            or (isinstance(focus, QWidget) and (focus is self.table or self.table.isAncestorOf(focus))))
                if in_table and event.key() == Qt.Key.Key_Delete and self.table.selectionModel().selectedRows():
                    return handle(self.delete_table_rows)
                if not in_table:
                    return handle(lambda: self.command(self.controller.delete))
            if ctrl and not shift and event.key() == Qt.Key.Key_Z:
                return handle(lambda: self.command(self.controller.undo))
            if (ctrl and event.key() == Qt.Key.Key_Y) or (ctrl and shift and event.key() == Qt.Key.Key_Z):
                return handle(lambda: self.command(self.controller.redo))
        return super().eventFilter(watched, event)
    def reset_all(self):
        self._pending_rows.clear()
        self._table_drafts.clear()
        return self.controller.reset()
    def fill_selected_gap(self, direction: str):
        if self.controller.state.selected:
            self.command(lambda: self.controller.fill_selected_gaps(self.controller.state.selected, direction))
    def defer_render(self):
        if not self._render_pending:
            self._render_pending = True
            QTimer.singleShot(0, self._render_deferred)
    def _render_deferred(self):
        self._render_pending = False
        self.render()
    def render(self):
        self.scene.clear(); pen = QPen(QColor("#888"))
        for minute in range(0, 1441, 5):
            x = LEFT + minute * SCALE; height = 22 if minute % 60 == 0 else (14 if minute % 15 == 0 else 7)
            self.scene.addLine(x, TOP - height, x, TOP, pen)
            if minute < 1440 and minute % 60 == 0:
                label = self.scene.addText(f"{minute // 60:02}:00"); label.setPos(x - 16, TOP - 45)
        self.cursor_line, self.cursor_tooltip = self._add_cursor(self.controller.state.cursor_minute)
        self.drag_end_line = self.drag_end_tooltip = None
        for segment in self.controller.projection.segments:
            item = SegmentItem(segment, self); item.setPen(QPen(QColor("white") if segment.source_id in self.controller.state.selected else QColor("black"), 3 if segment.source_id in self.controller.state.selected else 1)); self.scene.addItem(item)
        self.status.setText(self.controller.message or f"Cursor: {clock(self.controller.state.cursor_minute)}")
        self.refresh_table()

    def add_table_row(self, kind: BlockKind):
        ends = [segment.end_minute for segment in self.controller.projection.segments]
        ends.extend(row.get("end") for row in self._pending_rows if isinstance(row.get("end"), int))
        self._pending_rows.append({"kind": kind, "start": max(ends, default=0), "end": ""})
        self.refresh_table()

    def refresh_table(self):
        """Reflect the visible projection in the editable table without feedback."""
        self._syncing_table = True
        # A masked Work block can yield several visible rows. Their source ID
        # is retained only until an edit: replace_strict then removes that
        # authored source, deliberately making the table edit destructive.
        rows = [{"id": s.source_id, "segment_key": s.key, "kind": s.kind, "start": s.start_minute, "end": s.end_minute} for s in self.controller.projection.segments] + self._pending_rows
        self._table_rows = rows
        self.table.setRowCount(len(rows))
        for row_index, data in enumerate(rows):
            combo = TypeCombo()
            for kind in BlockKind: combo.addItem(KINDS[kind].label, kind.value)
            combo.setCurrentIndex(list(BlockKind).index(data["kind"]))
            draft = self._table_drafts.get(data.get("segment_key", ""))
            start = TimeEdit(draft[0] if draft else data.get("start_text", clock(data["start"])))
            end = TimeEdit(draft[1] if draft else data.get("end_text", clock(data["end"]) if isinstance(data["end"], int) else ""))
            end.set_drag_fallback(lambda box=start: parse_clock(box.text()))
            start.setPlaceholderText("HH:MM"); end.setPlaceholderText("HH:MM")
            combo.activated.connect(lambda _, d=data, c=combo: self.table_type_changed(d, c))
            start.editingFinished.connect(lambda d=data, c=combo, s=start, e=end: self.table_row_changed(d, c, s, e))
            end.editingFinished.connect(lambda d=data, c=combo, s=start, e=end: self.table_row_changed(d, c, s, e))
            start.timeDragged.connect(lambda d=data, c=combo, s=start, e=end: self.table_row_changed(d, c, s, e))
            end.timeDragged.connect(lambda d=data, c=combo, s=start, e=end: self.table_row_changed(d, c, s, e))
            for widget in (combo, start, end):
                widget.advanceRequested.connect(lambda w=widget: self.queue_table_advance(w))
                widget.escapeRequested.connect(self.unfocus_table)
            self.table.setCellWidget(row_index, 0, combo); self.table.setCellWidget(row_index, 1, start); self.table.setCellWidget(row_index, 2, end)
        self._syncing_table = False
        selected_rows = {index for index, data in enumerate(rows) if data.get("id") in self.controller.state.selected}
        self.apply_table_row_highlights(selected_rows)

    def queue_table_advance(self, widget):
        position = next(((row, column) for row in range(self.table.rowCount()) for column in range(self.table.columnCount()) if self.table.cellWidget(row, column) is widget), None)
        if position is not None:
            QTimer.singleShot(0, lambda p=position: self.focus_next_table_control(*p))

    def focus_next_table_control(self, row: int, column: int):
        for index in range(row * self.table.columnCount() + column + 1, self.table.rowCount() * self.table.columnCount()):
            widget = self.table.cellWidget(index // self.table.columnCount(), index % self.table.columnCount())
            if widget is not None:
                widget.setFocus()
                return
        self.unfocus_table()

    def unfocus_table(self):
        self.table.clearSelection()
        self.table.clearFocus()
        self.setFocus()

    def delete_table_rows(self):
        rows = sorted({index.row() for index in self.table.selectionModel().selectedRows()}, reverse=True)
        if not rows:
            return
        ids = {self._table_rows[row].get("id") for row in rows if self._table_rows[row].get("id")}
        if ids:
            self.controller.delete(ids)
        for row in rows:
            data = self._table_rows[row]
            if data in self._pending_rows:
                self._pending_rows.remove(data)
        self.render()

    def highlight_table_row(self, row: int):
        data = self._table_rows[row]
        self.apply_table_row_highlights({row})
        source_id = data.get("id")
        # Pending/invalid rows have no source ID and remain table-only.
        if source_id and any(block.id == source_id for block in self.controller.state.blocks):
            self.controller.select(source_id)
            self.defer_render()

    def apply_table_row_highlights(self, rows: set[int]):
        self.table.clearSelection()
        for row_index in range(self.table.rowCount()):
            selected = row_index in rows
            if selected:
                index = self.table.model().index(row_index, 0)
                self.table.selectionModel().select(index, QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
            for column in range(self.table.columnCount()):
                widget = self.table.cellWidget(row_index, column)
                if widget is not None:
                    widget.setStyleSheet("background: #435d78; color: #ffffff; border: 1px solid #7399bd;" if selected else "")

    def table_row_changed(self, data: dict, combo: QComboBox, start_box: QLineEdit, end_box: QLineEdit):
        if self._syncing_table: return
        start, end = parse_clock(start_box.text()), parse_clock(end_box.text())
        if start is None or end is None or start >= end:
            if "segment_key" in data:
                self._table_drafts[data["segment_key"]] = (start_box.text(), end_box.text())
            else:
                data["start_text"], data["end_text"] = start_box.text(), end_box.text()
            self.status.setText("Enter valid HH:MM times with an end after the start.")
            return
        kind = BlockKind(combo.currentData())
        changed = (self.controller.replace_visible_strict(data["segment_key"], kind, start, end)
                   if "segment_key" in data else self.controller.replace_strict(None, kind, start, end))
        if changed:
            self._table_drafts.pop(data.get("segment_key", ""), None)
            if data in self._pending_rows: self._pending_rows.remove(data)
            self.defer_render()

    def table_type_changed(self, data: dict, combo: QComboBox):
        if self._syncing_table:
            return
        kind = BlockKind(combo.currentData())
        if "segment_key" not in data:
            data["kind"] = kind
            return
        self.change_visible_kind(data["segment_key"], kind)

    def change_visible_kind(self, segment_key: str, kind: BlockKind):
        """Change from a widget/menu, then repaint after that event returns."""
        if self.controller.change_visible_kind(segment_key, kind):
            self.defer_render()

    def change_selected_kinds(self, source_ids, kind: BlockKind):
        if self.controller.change_selected_kinds(source_ids, kind):
            self.defer_render()

    def _add_cursor(self, minute: int):
        x = LEFT + minute * SCALE
        line = self.scene.addLine(x, TOP - 30, x, TOP + 60, QPen(QColor("red"), 2))
        tooltip = self.scene.addText(clock(minute))
        tooltip.setDefaultTextColor(QColor("#ff5b5b"))
        tooltip.setPos(x - tooltip.boundingRect().width() / 2, TOP - 72)
        tooltip.setZValue(2)
        return line, tooltip

    def _set_cursor_position(self, line, tooltip, minute: int):
        x = LEFT + minute * SCALE
        line.setLine(x, TOP - 30, x, TOP + 60)
        tooltip.setPlainText(clock(minute))
        tooltip.setPos(x - tooltip.boundingRect().width() / 2, TOP - 72)
    def preview_drag(self, item: SegmentItem, delta: int):
        """Paint an active drag immediately without replacing Qt's grabbed item."""
        self.controller.preview_move(item.segment.source_id, delta)
        segment = next((s for s in self.controller.projection.segments if s.source_id == item.segment.source_id and s.kind == item.segment.kind), None)
        if segment is None:
            return
        item.set_segment_geometry(segment)
        self._set_cursor_position(self.cursor_line, self.cursor_tooltip, self.controller.state.cursor_minute)
        if self.drag_end_line is None:
            self.drag_end_line, self.drag_end_tooltip = self._add_cursor(segment.end_minute)
        else:
            self._set_cursor_position(self.drag_end_line, self.drag_end_tooltip, segment.end_minute)
        self.status.setText(f"{KINDS[segment.kind].label}: {clock(segment.start_minute)} – {clock(segment.end_minute)} ({segment.end_minute - segment.start_minute} min)")
    def preview_resize(self, item: SegmentItem, edge: str, minute: int):
        self.controller.preview_resize_visible(item._gesture_key, edge, minute)
        segment = next((s for s in self.controller.projection.segments if s.source_id == item.segment.source_id and s.kind == item.segment.kind), None)
        if segment is None:
            return
        item.set_segment_geometry(segment)
        self._set_cursor_position(self.cursor_line, self.cursor_tooltip, segment.start_minute)
        if self.drag_end_line is None:
            self.drag_end_line, self.drag_end_tooltip = self._add_cursor(segment.end_minute)
        else:
            self._set_cursor_position(self.drag_end_line, self.drag_end_tooltip, segment.end_minute)
        self.status.setText(f"{KINDS[segment.kind].label}: {clock(segment.start_minute)} – {clock(segment.end_minute)} ({segment.end_minute - segment.start_minute} min)")
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            pos = self.view.mapFrom(self, event.position().toPoint()); point = self.view.mapToScene(pos)
            if TOP - 30 <= point.y() <= TOP + 65:
                self.command(lambda: self.controller.set_cursor(pointer_to_minute(point.x())))
if __name__ == "__main__":
    application = QApplication(sys.argv); window = TimelineWindow(); window.show(); sys.exit(application.exec())
