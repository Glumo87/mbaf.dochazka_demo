"""Run the attendance timeline demo."""
from __future__ import annotations

import sys
import re
import calendar as month_calendar
from datetime import date, timedelta
from PyQt6.QtCore import QEvent, QItemSelectionModel, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QFont, QKeySequence, QPen, QRegularExpressionValidator, QShortcut, QPainter
from PyQt6.QtCore import QRegularExpression
from PyQt6.QtWidgets import QApplication, QAbstractItemView, QComboBox, QGraphicsDropShadowEffect, QGraphicsRectItem, QGraphicsScene, QGraphicsTextItem, QGraphicsView, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu, QPushButton, QSizePolicy, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget

from dochazka.timeline import BlockKind, KINDS, TimelineController
from dochazka.calendar_store import DayScheduleStore, DayType

SCALE, LEFT, TOP = 1.05, 50, 55
EDGE_GRAB_WIDTH = 7
SNAP_MINUTES = 8

def clock(minute: int) -> str: return f"{minute // 60:02}:{minute % 60:02}"
def duration_clock(minute: int) -> str: return f"{minute // 60}:{minute % 60:02}"
def signed_duration(minute: int) -> str:
    return ("−" if minute < 0 else "+") + duration_clock(abs(minute))
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
    timePreviewed = pyqtSignal(int)
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
            self._set_text(clock(minute))
            # Keep the model history untouched, but let the editor show the
            # prospective boundary immediately while the pointer is moving.
            self.timePreviewed.emit(minute)
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
    cursorRequested = pyqtSignal(object)
    selectionStarted = pyqtSignal(object)
    selectionMoved = pyqtSignal(object)
    selectionFinished = pyqtSignal(object)

    def __init__(self, scene, parent=None):
        super().__init__(scene, parent)
        self._selection_origin = None
        self._selection_dragging = False

    def wheelEvent(self, event):
        delta = event.angleDelta().y() or event.angleDelta().x()
        self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta)
        event.accept()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            item = self.itemAt(event.position().toPoint())
            while item is not None:
                if isinstance(item, SegmentItem):
                    super().mousePressEvent(event)
                    return
                item = item.parentItem()
            self._selection_origin = event.position().toPoint()
            self._selection_dragging = False
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._selection_origin is None:
            super().mouseMoveEvent(event)
            return
        point = event.position().toPoint()
        if not self._selection_dragging and (point - self._selection_origin).manhattanLength() >= 3:
            self._selection_dragging = True
            self.selectionStarted.emit(self._selection_origin)
        if self._selection_dragging:
            self.selectionMoved.emit(point)
        event.accept()

    def mouseReleaseEvent(self, event):
        if self._selection_origin is None or event.button() != Qt.MouseButton.LeftButton:
            super().mouseReleaseEvent(event)
            return
        origin, dragging = self._selection_origin, self._selection_dragging
        self._selection_origin = None
        self._selection_dragging = False
        if dragging:
            self.selectionFinished.emit(event.position().toPoint())
        else:
            self.cursorRequested.emit(origin)
        event.accept()


class TimelineEditorPanel(QWidget):
    """Accept cursor placement anywhere in the timeline column's background."""
    cursorRequested = pyqtSignal(object)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.cursorRequested.emit(event.position().toPoint())
            event.accept()
            return
        super().mousePressEvent(event)

class SegmentItem(QGraphicsRectItem):
    def __init__(self, segment, window: "TimelineWindow"):
        width = (segment.end_minute - segment.start_minute) * SCALE
        super().__init__(0, 0, width, 54)
        self.segment, self.window = segment, window
        self._drag_start: int | None = None
        self._drag_button: Qt.MouseButton | None = None
        self._dragged = False
        self._fine_drag = False
        self._held_drag_buttons: set[Qt.MouseButton] = set()
        self._active_drag_button: Qt.MouseButton | None = None
        self._drag_delta_offset = 0
        self._last_drag_delta = 0
        self._drag_mode = "move"
        self._resize_anchor = 0
        self._resize_snap_boundaries: tuple[int, ...] = ()
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
        # The second mouse button is a momentary precision/no-snap modifier.
        # Rebase at this pointer position so changing sensitivity never jumps.
        if self._drag_start is not None and event.button() not in self._held_drag_buttons:
            self._held_drag_buttons.add(event.button())
            self._set_active_drag_button(event, event.button())
            event.accept()
            return
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
        self._drag_button = event.button()
        self._dragged = False
        self._fine_drag = event.button() == Qt.MouseButton.RightButton
        self._held_drag_buttons = {event.button()}
        self._active_drag_button = event.button()
        self._drag_delta_offset = 0
        self._last_drag_delta = 0
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
        self._resize_snap_boundaries = tuple(
            boundary
            for snap_segment in self._snap_segments
            if snap_segment.source_id not in self.window.controller.state.selected
            for boundary in (snap_segment.start_minute, snap_segment.end_minute)
            if boundary != self._resize_anchor
        ) + (0, 1440)
        self.window.controller.begin_gesture()
        event.accept()

    def hoverMoveEvent(self, event):
        on_edge = event.pos().x() <= EDGE_GRAB_WIDTH or event.pos().x() >= self.rect().width() - EDGE_GRAB_WIDTH
        self.setCursor(Qt.CursorShape.SizeHorCursor if on_edge else Qt.CursorShape.OpenHandCursor)
        event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_start is None:
            event.ignore(); return
        self._sync_drag_buttons(event)
        raw_delta = pointer_to_minute(event.scenePos().x()) - self._drag_start
        adjusted_delta = self._drag_delta_offset + (int(raw_delta / 5) if self._fine_drag else raw_delta)
        self._dragged = self._dragged or adjusted_delta != 0
        if self._drag_mode.startswith("resize-"):
            minute = self._resize_anchor + adjusted_delta
            if not self._fine_drag:
                minute = self.snapped_resize_minute(minute, event.modifiers())
            self._last_drag_delta = minute - self._resize_anchor
            self.window.preview_resize(self, self._drag_mode.removeprefix("resize-"), minute)
        else:
            delta = adjusted_delta
            if not self._fine_drag:
                delta = self.snapped_delta(delta, event.modifiers())
            self._last_drag_delta = delta
            self.window.preview_drag(self, delta)
        event.accept()

    def _rebase_drag(self, event, *, fine: bool):
        """Change drag sensitivity while preserving the current preview position."""
        self._drag_start = pointer_to_minute(event.scenePos().x())
        self._drag_delta_offset = self._last_drag_delta
        self._fine_drag = fine

    @staticmethod
    def _mouse_buttons(buttons) -> set[Qt.MouseButton]:
        return {button for button in (Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton) if buttons & button}

    def _set_active_drag_button(self, event, button: Qt.MouseButton):
        if button == self._active_drag_button:
            return
        self._active_drag_button = button
        self._rebase_drag(event, fine=button == Qt.MouseButton.RightButton)

    def _sync_drag_buttons(self, event):
        """Promote whichever held button most recently takes over the gesture."""
        live_buttons = self._mouse_buttons(event.buttons())
        added = live_buttons - self._held_drag_buttons
        self._held_drag_buttons = live_buttons
        if added:
            self._set_active_drag_button(event, next(iter(added)))
        elif self._active_drag_button not in live_buttons and live_buttons:
            self._set_active_drag_button(event, next(iter(live_buttons)))

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
                # Do not stick to the neighbour's boundary that we started
                # against; crossing it is what lets adjacent blocks merge.
                boundaries.extend(
                    boundary for boundary in (segment.start_minute, segment.end_minute)
                    if boundary not in (self._gesture_segment.start_minute, self._gesture_segment.end_minute)
                )
        corrections = [boundary - edge for edge in (start, end) for boundary in boundaries if abs(boundary - edge) <= SNAP_MINUTES]
        return delta + min(corrections, key=abs) if corrections else delta

    def snapped_resize_minute(self, minute: int, modifiers) -> int:
        """Snap a resize edge without re-snapping to its original neighbour."""
        if modifiers & (Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.ControlModifier):
            return minute
        corrections = [boundary - minute for boundary in self._resize_snap_boundaries if abs(boundary - minute) <= SNAP_MINUTES]
        return minute + min(corrections, key=abs) if corrections else minute

    def mouseReleaseEvent(self, event):
        if self._drag_start is None:
            event.ignore(); return
        remaining_buttons = self._mouse_buttons(event.buttons())
        if remaining_buttons:
            self._held_drag_buttons = remaining_buttons
            if self._active_drag_button not in remaining_buttons:
                self._set_active_drag_button(event, next(iter(remaining_buttons)))
            event.accept()
            return
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
        self._drag_button = None
        self._held_drag_buttons.clear()
        self._active_drag_button = None
        # Do not clear the graphics scene while Qt is still dispatching this
        # event to this item; schedule rebuilding after it has returned.
        self.window.defer_render()
        event.accept()

class DayCell(QWidget):
    """Compact calendar day with a readable timeline-duration summary."""
    clicked = pyqtSignal(object)
    action_requested = pyqtSignal(object, str)

    def __init__(self, day: date, parent=None):
        super().__init__(parent)
        self.day = day
        self.selected = False
        self.in_month = True
        self.day_type = DayType.WORKDAY
        self.work_minutes = 0
        self.summary: tuple[tuple[BlockKind, int], ...] = ()
        self.setMinimumHeight(78)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.actions = QPushButton(self)
        self.actions.setText("Type")
        self.actions.setToolTip("Set day type")
        menu = QMenu(self.actions)
        for label, action in (("Workday — editable", DayType.WORKDAY.value),
                              ("Vacation — read only", DayType.VACATION.value),
                              ("Sick leave — read only", DayType.SICK_LEAVE.value)):
            item = menu.addAction(label); item.setData(action)
        menu.triggered.connect(lambda item: self.action_requested.emit(self.day, item.data()))
        self.actions.clicked.connect(lambda: menu.popup(self.actions.mapToGlobal(self.actions.rect().bottomLeft())))

    def resizeEvent(self, event):
        self.actions.setGeometry(7, 27, 58, 27)
        super().resizeEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit(self.day)
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        weekend = self.day.weekday() >= 5
        background = QColor("#f0c28f" if self.selected else ("#e7ddd0" if weekend else "#f4efe5"))
        if not self.in_month:
            background = QColor("#ddd7cc" if weekend else "#e9e4da")
        painter.fillRect(self.rect(), background)
        painter.setPen(QPen(QColor("#c76d28") if self.selected else QColor("#b8b0a4"), 2 if self.selected else 1))
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))
        painter.setPen(QColor("#34312c" if self.in_month else "#827b70"))
        font = painter.font(); font.setPointSize(12); font.setBold(True); painter.setFont(font)
        painter.drawText(7, 17, str(self.day.day))
        detail_font = painter.font(); detail_font.setPointSize(10); detail_font.setBold(False); painter.setFont(detail_font)
        x, y = 77, 21
        if self.day_type is not DayType.WORKDAY:
            painter.setPen(QColor("#6f8054" if self.day_type is DayType.VACATION else "#9c5146"))
            painter.drawText(x, y, "Vacation — read only" if self.day_type is DayType.VACATION else "Sick leave — read only")
            y += 14
        for kind, minutes in self.summary:
            painter.setPen(QColor(KINDS[kind].color).darker(150))
            painter.drawText(x, y, f"{KINDS[kind].label}: {duration_clock(minutes)}")
            y += 14


class MonthOverview(QWidget):
    """A calendar grid whose contents are derived from per-day projections."""
    day_selected = pyqtSignal(object)
    day_type_requested = pyqtSignal(object, str)

    def __init__(self, schedules: DayScheduleStore, parent=None):
        super().__init__(parent)
        self.schedules = schedules
        selected = schedules.selected_date
        self.month = selected.replace(day=1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 4)
        header = QHBoxLayout()
        previous = QPushButton("‹")
        previous.setAccessibleName("Previous month")
        previous.clicked.connect(lambda: self.shift_month(-1))
        following = QPushButton("›")
        following.setAccessibleName("Next month")
        following.clicked.connect(lambda: self.shift_month(1))
        self.title = QLabel()
        self.title.setStyleSheet("font-size: 17px; font-weight: 600;")
        self.total = QLabel()
        self.total.setStyleSheet("color: #5d574e; font-weight: 600; padding: 4px 8px; background: #ead8c2; border: 1px solid #c59d76; border-radius: 4px;")
        header.addWidget(previous); header.addWidget(following); header.addWidget(self.title); header.addStretch(1); header.addWidget(self.total)
        layout.addLayout(header)
        labels = QGridLayout()
        labels.setSpacing(3)
        for column, name in enumerate(("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun", "Week summary")):
            label = QLabel(name); label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setStyleSheet("color: #655e54; font-weight: 600;")
            labels.addWidget(label, 0, column)
        self.grid = QGridLayout()
        self.grid.setSpacing(3)
        for column in range(7):
            labels.setColumnStretch(column, 1); self.grid.setColumnStretch(column, 1)
        labels.setColumnMinimumWidth(7, 150); self.grid.setColumnMinimumWidth(7, 150)
        layout.addLayout(labels)
        layout.addLayout(self.grid, 1)
        self.refresh()

    def shift_month(self, delta: int):
        absolute = self.month.year * 12 + self.month.month - 1 + delta
        self.month = date(absolute // 12, absolute % 12 + 1, 1)
        self.refresh()

    def select(self, day: date):
        self.day_selected.emit(day)

    def refresh(self):
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        first_weekday, days = month_calendar.monthrange(self.month.year, self.month.month)
        first = self.month - timedelta(days=first_weekday)
        for index in range(42):
            day = first + timedelta(days=index)
            cell = DayCell(day)
            cell.in_month = day.month == self.month.month
            cell.selected = day == self.schedules.selected_date
            cell.day_type = self.schedules.day_type(day)
            controller = self.schedules.controller_for(day)
            if controller is not None:
                projection = controller.projection
                cell.work_minutes = sum(segment.end_minute - segment.start_minute for segment in projection.segments if segment.kind is BlockKind.WORK)
                durations = {kind: 0 for kind in BlockKind}
                for segment in projection.segments:
                    durations[segment.kind] += segment.end_minute - segment.start_minute
                cell.summary = tuple((kind, durations[kind]) for kind in BlockKind if durations[kind])
            cell.clicked.connect(self.select)
            cell.action_requested.connect(self.action_requested)
            self.grid.addWidget(cell, index // 7, index % 7)
        for row in range(6):
            self.grid.addWidget(WeekSummary(first + timedelta(days=row * 7), self.schedules), row, 7)
        total = 0
        vacation_days = 0
        sick_days = 0
        for day, controller in self.schedules.known_schedules():
            if day.year == self.month.year and day.month == self.month.month:
                total += sum(s.end_minute - s.start_minute for s in controller.projection.segments if s.kind is BlockKind.WORK)
                if self.schedules.day_type(day) is DayType.VACATION:
                    vacation_days += 1
                elif self.schedules.day_type(day) is DayType.SICK_LEAVE:
                    sick_days += 1
        self.title.setText(self.month.strftime("%B %Y"))
        parts = [f"{total / 60:g} h worked"]
        if vacation_days: parts.append(f"{vacation_days} vacation")
        if sick_days: parts.append(f"{sick_days} sick")
        self.total.setText("  •  ".join(parts))

    def action_requested(self, day: date, action: str):
        self.day_type_requested.emit(day, action)


class WeekSummary(QWidget):
    """Always-visible summary aligned with one row of calendar days."""
    def __init__(self, week_start: date, schedules: DayScheduleStore, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(150)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(9, 8, 8, 6)
        layout.setSpacing(2)
        total = 0
        vacation_days = 0
        sick_days = 0
        for offset in range(7):
            day = week_start + timedelta(days=offset)
            controller = schedules.controller_for(day)
            minutes = 0 if controller is None else sum(s.end_minute - s.start_minute for s in controller.projection.segments if s.kind is BlockKind.WORK)
            total += minutes
            day_type = schedules.day_type(day)
            if day_type is DayType.VACATION: vacation_days += 1
            elif day_type is DayType.SICK_LEAVE: sick_days += 1
        label = QLabel(f"Week {week_start.isocalendar().week}\nWork: {total / 60:g} h")
        label.setStyleSheet("color: #403b34; font-size: 13px;")
        layout.addWidget(label)
        if vacation_days or sick_days:
            states = []
            if vacation_days: states.append(f"Vacation: {vacation_days}")
            if sick_days: states.append(f"Sick: {sick_days}")
            state = QLabel("  ·  ".join(states))
            state.setStyleSheet("color: #9c5146; font-weight: 600;")
            layout.addWidget(state)
        layout.addStretch(1)
        self.setStyleSheet("background: #ead8c2; border: 1px solid #c59d76; border-radius: 4px;")


class DayTypeControl(QWidget):
    """Small extensible horizontal traffic-light control for a ledger row."""
    type_requested = pyqtSignal(str)
    OPTIONS = (
        (DayType.WORKDAY, "☭", "Workday"),
        (DayType.VACATION, "☀", "Vacation — read only"),
        (DayType.SICK_LEAVE, "✚", "Sick leave — read only"),
    )

    def __init__(self, day_type: DayType, row_background: str = "#f5f1e8", parent=None):
        super().__init__(parent)
        self.row_background = row_background
        self._paint_background = row_background
        self._buttons: list[tuple[QPushButton, bool]] = []
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 3, 4, 3)
        layout.setSpacing(3)
        for option, symbol, tooltip in self.OPTIONS:
            button = QPushButton(symbol)
            button.setMinimumHeight(25)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            button.setFont(QFont("Segoe UI Symbol", 13))
            button.setCheckable(True)
            button.setChecked(option is day_type)
            button.setFlat(option is not day_type)
            button.setAutoFillBackground(False)
            button.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, option is not day_type)
            button.setToolTip(tooltip)
            self._buttons.append((button, option is day_type))
            button.clicked.connect(lambda _, value=option.value: self.type_requested.emit(value))
            layout.addWidget(button)
        self.set_row_selected(False)

    def set_row_selected(self, selected: bool):
        """Leave inactive controls transparent while this cell paint owns its colour."""
        # The underlying QTableWidget paints both the weekend tone and the
        # selected-row highlight.  Painting a second background here creates
        # pale rectangles between buttons, so only the active option is drawn.
        self._paint_background = "#f0c28f" if selected else self.row_background
        for button, active in self._buttons:
            button.setStyleSheet(
                f"QPushButton {{ background: {'#d77b32' if active else 'transparent'}; "
                f"color: {'#fffaf2' if active else '#56514a'}; border: none; "
                f"border-radius: 4px; padding: 0; font-weight: 600; }}"
                f"QPushButton:hover {{ background: {'#c76d28' if active else '#e7b170'}; color: {'#fffaf2' if active else '#403b34'}; }}"
            )

    def paintEvent(self, event):
        # Fill the whole cell behind the transparent inactive buttons.
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(self._paint_background))


class DayBarCell(QWidget):
    """A text-free miniature 24-hour projection for one ledger day."""

    def __init__(self, day: date, controller: TimelineController | None, row_background: str, parent=None):
        super().__init__(parent)
        self.day = day
        self.controller = controller
        self.row_background = row_background
        self.setMinimumWidth(110)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setToolTip(day.strftime("%A") + " — miniature timeline")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(self.row_background))
        painter.setPen(Qt.PenStyle.NoPen)
        if self.controller is None:
            return
        segments = self.controller.projection.segments
        if not segments:
            return
        first = min(segment.start_minute for segment in segments)
        last = max(segment.end_minute for segment in segments)
        span = max(1, last - first)
        for segment in segments:
            left = round((segment.start_minute - first) / span * self.width())
            right = round((segment.end_minute - first) / span * self.width())
            painter.setBrush(QColor(KINDS[segment.kind].color))
            painter.drawRect(left, 5, max(1, right - left), max(1, self.height() - 10))


class MonthLedger(QWidget):
    """A dense monthly view with weekly expected-versus-credited balances."""
    day_selected = pyqtSignal(object)
    day_open_requested = pyqtSignal(object)
    day_type_requested = pyqtSignal(object, str)
    detail_requested = pyqtSignal()

    def __init__(self, schedules: DayScheduleStore, parent=None):
        super().__init__(parent)
        self.schedules = schedules
        self.month = schedules.selected_date.replace(day=1)
        self._type_click_sources: set[QWidget] = set()
        self._suppress_day_select = False
        self._pending_day_click: date | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 4)
        header = QHBoxLayout()
        previous = QPushButton("Previous month")
        previous.clicked.connect(lambda: self.shift_month(-1))
        following = QPushButton("Next month")
        following.clicked.connect(lambda: self.shift_month(1))
        self.title = QLabel(); self.title.setStyleSheet("font-size: 17px; font-weight: 600;")
        self.total = QLabel(); self.total.setStyleSheet("color: #5d574e; font-weight: 600; padding: 4px 8px; background: #ead8c2; border: 1px solid #c59d76; border-radius: 4px;")
        self.detail_button = QPushButton("Show day editor")
        self.detail_button.clicked.connect(self.detail_requested)
        header.addWidget(previous); header.addWidget(following); header.addWidget(self.title); header.addStretch(1); header.addWidget(self.total); header.addWidget(self.detail_button)
        layout.addLayout(header)
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(["Date", "Day", "Day type", "Expected", "Credited", "Week balance", "Note", ""])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setFixedWidth(960)
        widths = (78, 108, 116, 82, 82, 104, 210, 120)
        for column, width in enumerate(widths):
            self.table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            self.table.setColumnWidth(column, width)
        self.table.cellClicked.connect(self._row_clicked)
        self.table.cellDoubleClicked.connect(self._row_double_clicked)
        self.table.itemSelectionChanged.connect(self._sync_type_control_backgrounds)
        table_row = QHBoxLayout()
        table_row.setContentsMargins(0, 0, 0, 0)
        table_row.addWidget(self.table)
        self.summary_panel = QWidget()
        self.summary_panel.setFixedWidth(230)
        self.summary_panel.setStyleSheet("background: #f4efe5; border: 1px solid #c9bdad; border-radius: 9px;")
        summary_layout = QVBoxLayout(self.summary_panel)
        summary_layout.setContentsMargins(15, 14, 15, 14)
        summary_layout.setSpacing(7)
        heading = QLabel("MONTHLY OVERVIEW")
        heading.setStyleSheet("color: #766c60; font-size: 10px; font-weight: 700; border: none;")
        self.month_balance = QLabel()
        self.month_balance.setStyleSheet("font-size: 24px; font-weight: 700; border: none;")
        self.month_expected = QLabel(); self.month_credited = QLabel(); self.month_days = QLabel()
        for label in (self.month_expected, self.month_credited, self.month_days):
            label.setStyleSheet("color: #514b43; border: none;")
        note = QLabel("Weekly balances reset on Monday.")
        note.setWordWrap(True)
        note.setStyleSheet("color: #766c60; font-size: 11px; border: none;")
        summary_layout.addWidget(heading); summary_layout.addWidget(self.month_balance)
        summary_layout.addWidget(self.month_expected); summary_layout.addWidget(self.month_credited); summary_layout.addWidget(self.month_days)
        summary_layout.addStretch(1); summary_layout.addWidget(note)
        table_row.addWidget(self.summary_panel)
        table_row.addStretch(1)
        layout.addLayout(table_row, 1)
        self.refresh()

    @staticmethod
    def _minutes(controller: TimelineController | None) -> int:
        return 0 if controller is None else sum(
            segment.end_minute - segment.start_minute
            for segment in controller.projection.segments
            if segment.kind is BlockKind.WORK
        )

    def _credited(self, day: date) -> int:
        if day.weekday() >= 5:
            return 0
        if self.schedules.day_type(day) is not DayType.WORKDAY:
            return 480
        return self._minutes(self.schedules.controller_for(day))

    def shift_month(self, delta: int):
        absolute = self.month.year * 12 + self.month.month - 1 + delta
        self.month = date(absolute // 12, absolute % 12 + 1, 1)
        self.refresh()

    def _set_item(self, row: int, column: int, text: str, color: str | None = None):
        item = QTableWidgetItem(text)
        if color:
            item.setForeground(QColor(color))
        self.table.setItem(row, column, item)

    def _type_button(self, day: date, row_background: str) -> DayTypeControl:
        control = DayTypeControl(self.schedules.day_type(day), row_background)
        control.type_requested.connect(lambda action, value=day: self.day_type_requested.emit(value, action))
        self._type_click_sources.add(control)
        control.installEventFilter(self)
        for button in control.findChildren(QPushButton):
            self._type_click_sources.add(button)
            button.installEventFilter(self)
        return control

    def eventFilter(self, watched, event):
        if watched in self._type_click_sources and event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease):
            self._suppress_day_select = True
            QTimer.singleShot(0, lambda: setattr(self, "_suppress_day_select", False))
        return super().eventFilter(watched, event)

    def set_detail_visible(self, visible: bool):
        self.detail_button.setText("Hide day editor" if visible else "Show day editor")

    def _row_clicked(self, row: int, column: int):
        # The segmented day-type control lives in this column.  Its click must
        # never be interpreted as selecting a new day, otherwise Qt redraws
        # the complete timeline before the lightweight type update runs.
        if column == 2 or self._suppress_day_select:
            return
        day = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        if isinstance(day, date):
            # A single click changes the active day, but give Qt a short
            # window to deliver cellDoubleClicked before rebuilding the table.
            self._pending_day_click = day
            QTimer.singleShot(180, self._commit_pending_day_click)

    def _commit_pending_day_click(self):
        day, self._pending_day_click = self._pending_day_click, None
        if isinstance(day, date):
            self.day_selected.emit(day)

    def _row_double_clicked(self, row: int, column: int):
        """Open a real calendar day, while leaving week-total rows inert."""
        item = self.table.item(row, 0)
        day = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if isinstance(day, date):
            self._pending_day_click = None
            self.day_open_requested.emit(day)

    def _sync_type_control_backgrounds(self):
        selected_rows = {index.row() for index in self.table.selectionModel().selectedRows()}
        for row in range(self.table.rowCount()):
            control = self.table.cellWidget(row, 2)
            if isinstance(control, DayTypeControl):
                control.set_row_selected(row in selected_rows)

    def selected_day(self) -> date | None:
        """Return the date represented by the currently selected ledger row."""
        item = self.table.item(self.table.currentRow(), 0)
        day = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        return day if isinstance(day, date) else None

    def refresh(self):
        self._type_click_sources.clear()
        vertical_scroll = self.table.verticalScrollBar().value()
        horizontal_scroll = self.table.horizontalScrollBar().value()
        # clearContents removes old embedded type controls before rows are
        # reused, preventing controls/data from leaking into week-total rows.
        self.table.clearContents()
        year, month = self.month.year, self.month.month
        days = month_calendar.monthrange(year, month)[1]
        today = date.today()
        entries: list[tuple[date | None, tuple[int, int, int] | None]] = []
        week_days: list[date] = []
        for day_number in range(1, days + 1):
            day = date(year, month, day_number)
            week_days.append(day)
            entries.append((day, None))
            if day.weekday() == 6 or day_number == days:
                expected = sum(480 for value in week_days if value.weekday() < 5)
                credited = sum(self._credited(value) for value in week_days)
                entries.append((None, (expected, credited, credited - expected)))
                week_days.clear()
        self.table.setRowCount(len(entries))
        monthly_credited = 0
        vacation_days = sick_days = 0
        for row, (day, subtotal) in enumerate(entries):
            if day is None:
                expected, credited, balance = subtotal
                self._set_item(row, 0, "Week total")
                self._set_item(row, 1, "")
                self._set_item(row, 2, "")
                self._set_item(row, 3, duration_clock(expected))
                self._set_item(row, 4, duration_clock(credited))
                self._set_item(row, 5, signed_duration(balance), "#b85c1b" if balance < 0 else "#526c3e")
                self._set_item(row, 6, "Balance resets Monday")
                self._set_item(row, 7, "")
                for column in range(8):
                    item = self.table.item(row, column)
                    item.setBackground(QColor("#ead8c2"))
                    item.setFlags(Qt.ItemFlag.NoItemFlags)
                continue
            active = True
            day_type = self.schedules.day_type(day)
            expected = 480 if day.weekday() < 5 else 0
            credited = self._credited(day) if active else 0
            weekly_start = day - timedelta(days=day.weekday())
            days_so_far = [
                weekly_start + timedelta(days=offset)
                for offset in range(day.weekday() + 1)
                if (weekly_start + timedelta(days=offset)).year == year and (weekly_start + timedelta(days=offset)).month == month
            ]
            running_expected = sum(480 for value in days_so_far if value.weekday() < 5)
            running_credited = sum(self._credited(value) for value in days_so_far)
            balance = running_credited - running_expected
            row_background = "#f1e3cf" if day.weekday() == 5 else "#ead3bb" if day.weekday() == 6 else "#f5f1e8"
            self._set_item(row, 0, day.strftime("%d.%m."))
            self.table.item(row, 0).setData(Qt.ItemDataRole.UserRole, day)
            self._set_item(row, 1, day.strftime("%A"))
            control = self._type_button(day, row_background)
            self.table.setCellWidget(row, 2, control)
            self._set_item(row, 3, duration_clock(expected) if active else "—")
            self._set_item(row, 4, duration_clock(credited) if active else "—")
            self._set_item(row, 5, signed_duration(balance) if active else "—", "#b85c1b" if active and balance < 0 else "#526c3e")
            note = ("Weekend" if day.weekday() >= 5 else "" if day_type is DayType.WORKDAY
                    else "Vacation credit" if day_type is DayType.VACATION else "Sick-leave credit")
            self._set_item(row, 6, note)
            row_background = "#f1e3cf" if day.weekday() == 5 else "#ead3bb" if day.weekday() == 6 else "#f5f1e8"
            self.table.setCellWidget(row, 7, DayBarCell(day, self.schedules.controller_for(day), row_background))
            if day.weekday() >= 5:
                # Weekends use two distinct but quiet warm tones; week totals
                # above retain their separate stronger summary background.
                weekend = QColor(row_background)
                for column in range(7):
                    item = self.table.item(row, column)
                    if item is not None:
                        item.setBackground(weekend)
            if day.weekday() < 5:
                if day_type is DayType.VACATION: vacation_days += 1
                elif day_type is DayType.SICK_LEAVE: sick_days += 1
            monthly_credited += credited
            if day == self.schedules.selected_date:
                self.table.selectRow(row)
        self.table.verticalScrollBar().setValue(vertical_scroll)
        self.table.horizontalScrollBar().setValue(horizontal_scroll)
        self.title.setText(self.month.strftime("%B %Y"))
        parts = [f"{duration_clock(monthly_credited)} credited"]
        if vacation_days: parts.append(f"{vacation_days} vacation")
        if sick_days: parts.append(f"{sick_days} sick")
        self.total.setText("  •  ".join(parts))
        expected_month = sum(480 for day_number in range(1, days + 1) if date(year, month, day_number).weekday() < 5)
        balance_month = monthly_credited - expected_month
        self.month_balance.setText(signed_duration(balance_month))
        self.month_balance.setStyleSheet(f"color: {'#b85c1b' if balance_month < 0 else '#526c3e'}; font-size: 24px; font-weight: 700; border: none;")
        self.month_expected.setText(f"Expected: {duration_clock(expected_month)}")
        self.month_credited.setText(f"Credited: {duration_clock(monthly_credited)}")
        extras = []
        if vacation_days: extras.append(f"{vacation_days} vacation")
        if sick_days: extras.append(f"{sick_days} sick")
        self.month_days.setText(" · ".join(extras) if extras else "No leave days")
        self._sync_type_control_backgrounds()


class TimelineWindow(QWidget):
    def __init__(self):
        super().__init__(); self.schedules = DayScheduleStore(); self.controller = self.schedules.controller; self.setWindowTitle("Docházka timeline")
        self.resize(1200, 760)
        self.setMinimumSize(980, 620)
        QApplication.instance().installEventFilter(self)
        self._handled_shortcut: tuple[int, Qt.KeyboardModifiers] | None = None
        self.setStyleSheet("""
            QWidget { background: #ebe7de; color: #34312c; }
            QPushButton { background: #d77b32; color: #fffaf2; border: 1px solid #a95820; border-radius: 7px; padding: 5px 9px; font-weight: 600; }
            QPushButton:hover { background: #c76d28; }
            QPushButton:pressed { background: #ac5923; }
            QPushButton:disabled { background: #c9c2b7; color: #847d73; border-color: #b5aea4; }
            QLineEdit, QComboBox { background: #faf7f0; color: #34312c; border: 1px solid #b8b0a4; border-radius: 7px; padding: 4px 8px; }
            QLineEdit:focus, QComboBox:focus { border: 2px solid #d77b32; }
            QComboBox::drop-down { border: none; width: 22px; }
            QGraphicsView { background: #f5f1e8; border: 2px solid #a79e91; border-radius: 7px; }
            QTableWidget { background: #f5f1e8; color: #34312c; border: 1px solid #b8b0a4; border-radius: 7px; gridline-color: #d5cec2; }
            QTableWidget::item { padding: 0; }
            QTableWidget::item:selected { background: #f0c28f; color: #34312c; }
            QHeaderView::section { background: #ded7ca; color: #34312c; border: none; border-bottom: 1px solid #b8b0a4; padding: 5px; font-weight: 600; }
            QTableCornerButton::section { background: #ded7ca; border: none; }
            #editorTimeline { background: #f7f3eb; border-color: #9f968a; }
            QTableWidget#editorRows { background: #f7f3eb; gridline-color: #c7bfb4; }
            QTableWidget#editorRows QHeaderView::section, QTableWidget#editorRows QTableCornerButton::section { background: #e5ddd1; }
            QScrollBar:vertical { background: transparent; width: 12px; margin: 5px 2px; }
            QScrollBar::handle:vertical { background: #c5b8a6; border-radius: 5px; min-height: 28px; }
            QScrollBar::handle:vertical:hover { background: #a99a86; }
            QScrollBar:horizontal { background: transparent; height: 12px; margin: 2px 5px; }
            QScrollBar::handle:horizontal { background: #c5b8a6; border-radius: 5px; min-width: 28px; }
            #editorTimeline QScrollBar:horizontal { background: #d5cabc; border: 1px solid #a99987; border-radius: 5px; }
            #editorTimeline QScrollBar::handle:horizontal { background: #806b55; border: 1px solid #5f4d3c; border-radius: 4px; min-width: 32px; }
            #editorTimeline QScrollBar::handle:horizontal:hover { background: #694f39; }
            QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
        """)
        self.scene = QGraphicsScene(self); self.view = TimelineView(self.scene); self.view.setObjectName("editorTimeline"); self.view.setMinimumWidth(720); self.view.setFixedHeight(150); self.view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff); self.view.cursorRequested.connect(self.set_cursor_from_view); self.view.selectionStarted.connect(self.begin_timeline_selection); self.view.selectionMoved.connect(self.update_timeline_selection); self.view.selectionFinished.connect(self.finish_timeline_selection); self._render_pending = False
        self._selection_origin = None
        self._selection_window: QGraphicsRectItem | None = None
        self._center_timeline_on_open = False
        self._syncing_table = False; self._pending_rows: list[dict] = []; self._table_drafts: dict[str, tuple[str, str]] = {}
        self._table_preview_gesture: str | None = None
        self._day_table_state: dict[date, tuple[list[dict], dict[str, tuple[str, str]]]] = {}
        layout = QVBoxLayout(self); layout.setContentsMargins(6, 6, 6, 6)
        self.month_overview = MonthLedger(self.schedules)
        self.month_overview.day_selected.connect(self.select_day)
        self.month_overview.day_open_requested.connect(self.open_day_editor)
        self.month_overview.day_type_requested.connect(self.queue_day_type)
        self.month_overview.detail_requested.connect(self.toggle_editor)
        layout.addWidget(self.month_overview)

        # The editor is deliberately not part of the page layout.  It is a
        # bottom sheet over the month ledger, so opening it never reflows the
        # calendar above it.
        self.editor = QWidget(self)
        self.editor.setObjectName("dayEditorSheet")
        self.editor.setStyleSheet(
            "#dayEditorSheet { background: #ded9d0; border: 1px solid #a99b89; border-radius: 12px; }"
            "#editorContent, #editorTablePane { background: #ded9d0; }"
        )
        shadow = QGraphicsDropShadowEffect(self.editor)
        shadow.setBlurRadius(24); shadow.setOffset(0, -3); shadow.setColor(QColor(52, 49, 44, 90))
        self.editor.setGraphicsEffect(shadow)
        sheet = QVBoxLayout(self.editor); sheet.setContentsMargins(14, 10, 14, 12); sheet.setSpacing(6)
        sheet_header = QHBoxLayout(); sheet_header.setContentsMargins(0, 0, 0, 0)
        self.editor_title = QLabel()
        self.editor_title.setStyleSheet("background: transparent; color: #655e54; font-size: 11px; font-weight: 700; letter-spacing: 1px; border: none;")
        close_editor = QPushButton("Close")
        close_editor.setToolTip("Close day editor (Esc)")
        close_editor.clicked.connect(lambda: self.set_editor_visible(False))
        sheet_header.addWidget(self.editor_title); sheet_header.addStretch(1); sheet_header.addWidget(close_editor)
        sheet.addLayout(sheet_header)
        self.editor_content = QWidget()
        self.editor_content.setObjectName("editorContent")
        body = QHBoxLayout(self.editor_content); body.setContentsMargins(0, 0, 0, 0); body.setSpacing(12)
        self.timeline_panel = TimelineEditorPanel()
        self.timeline_panel.cursorRequested.connect(self.set_cursor_from_panel)
        timeline_panel = QVBoxLayout(self.timeline_panel); timeline_panel.setContentsMargins(0, 0, 0, 0); buttons = QHBoxLayout()
        for kind in BlockKind:
            button = QPushButton(KINDS[kind].label)
            button.setToolTip("Add this block at the red timeline cursor")
            button.clicked.connect(lambda _, k=kind: self.command(lambda: self.controller.add(k))); buttons.addWidget(button)
        copy_previous = QPushButton("Copy previous")
        copy_previous.setToolTip("Copy the most recent earlier workday without vacation or doctor blocks")
        copy_previous.clicked.connect(self.copy_previous_schedule)
        buttons.addWidget(copy_previous)
        # Resolve the controller at click time: selecting another calendar day
        # replaces self.controller, while keyboard shortcuts already do this.
        for label, action in [
            ("Close gaps", lambda: self.controller.close_selected_gaps()),
            ("Undo", lambda: self.controller.undo()),
            ("Redo", lambda: self.controller.redo()),
            ("Reset", self.reset_all),
        ]:
            b = QPushButton(label); b.clicked.connect(lambda _, f=action: self.command(f)); buttons.addWidget(b)
        timeline_panel.addLayout(buttons); timeline_panel.addWidget(self.view); self.status = QLabel(); self.status.setStyleSheet("background: transparent;"); timeline_panel.addWidget(self.status)
        body.addWidget(self.timeline_panel, 1)
        table_container = QWidget(); table_container.setObjectName("editorTablePane"); table_container.setFixedWidth(360)
        table_panel = QVBoxLayout(table_container); table_panel.setContentsMargins(0, 0, 0, 0); table_panel.setSpacing(0)
        self.table = QTableWidget(0, 3); self.table.setObjectName("editorRows"); self.table.setHorizontalHeaderLabels(["Type", "Start", "End"]); self.table.setMinimumWidth(0)
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
        body.addWidget(table_container, 0)
        sheet.addWidget(self.editor_content, 1)
        self._editor_visible = False
        self.month_overview.set_detail_visible(False)
        self._update_editor_title()
        self._layout_editor_sheet()
        self.editor.hide()
        self.render()

    def select_day(self, day: date):
        """Swap the editor to an independent schedule without losing drafts."""
        editor_was_visible = self._editor_visible
        current = self.schedules.selected_date
        self._day_table_state[current] = (self._pending_rows, self._table_drafts)
        self.controller = self.schedules.select(day)
        self._pending_rows, self._table_drafts = self._day_table_state.get(day, ([], {}))
        self._update_editor_title()
        # Keep the sheet header interactive on read-only days: only its
        # timeline/table content is disabled, so Close is always available.
        self.editor_content.setEnabled(self.schedules.day_type(day) is DayType.WORKDAY)
        if day.month != self.month_overview.month.month or day.year != self.month_overview.month.year:
            self.month_overview.month = day.replace(day=1)
        self.set_editor_visible(False)
        # Do not rebuild the ledger after a single click while the sheet is
        # closed. Rebuilding it here discards Qt's pending second click, which
        # prevents cellDoubleClicked from ever firing.
        if editor_was_visible:
            self.render()

    def set_editor_visible(self, visible: bool):
        was_visible = self._editor_visible
        self._editor_visible = visible
        self.editor.setVisible(visible)
        self.month_overview.set_detail_visible(visible)
        if visible:
            self._center_timeline_on_open = True
            self._layout_editor_sheet()
            self.editor.raise_()
        elif was_visible:
            # Miniature day timelines are intentionally committed to the
            # ledger only when editing ends, not after each keystroke/drag.
            self.month_overview.refresh()
            # Closing a non-modal sheet must return keyboard navigation to
            # the ledger it overlays. Queue this after the hide/refresh so Qt
            # cannot leave focus on a now-hidden editor button.
            QTimer.singleShot(0, self.focus_month_ledger)

    def focus_month_ledger(self):
        self.month_overview.table.setFocus(Qt.FocusReason.OtherFocusReason)

    def _layout_editor_sheet(self):
        """Anchor the non-modal day editor to the bottom without moving the ledger."""
        sheet_height = min(274, max(210, self.height() - 90))
        self.editor.setGeometry(18, self.height() - sheet_height - 18, max(400, self.width() - 36), sheet_height)

    def _update_editor_title(self):
        self.editor_title.setText(f"DAY EDITOR · {self.schedules.selected_date.strftime('%a, %d %b')}")

    def _center_timeline_for_open(self):
        """Center a newly opened editor around its meaningful time range."""
        segments = self.controller.projection.segments
        minute = ((min(segment.start_minute for segment in segments) + max(segment.end_minute for segment in segments)) // 2
                  if segments else 720)
        # Setting the scroll bar directly is reliable after a hidden graphics
        # view has just been shown; QGraphicsView.centerOn can be ignored
        # until a later repaint on this platform.
        target_x = LEFT + minute * SCALE
        self.view.horizontalScrollBar().setValue(round(target_x - self.view.viewport().width() / 2))

    @staticmethod
    def _rounded_cursor_minute(scene_x: float) -> int:
        return max(0, min(1440, round(pointer_to_minute(scene_x) / 5) * 5))

    def set_cursor_from_view(self, view_pos):
        point = self.view.mapToScene(view_pos)
        self.command(lambda: self.controller.set_cursor(self._rounded_cursor_minute(point.x())))

    def set_cursor_from_panel(self, panel_pos):
        view_pos = self.view.mapFrom(self.timeline_panel, panel_pos)
        self.set_cursor_from_view(view_pos)

    def begin_timeline_selection(self, view_pos):
        """Start a rubber-band selection from empty timeline space."""
        self._selection_origin = self.view.mapToScene(view_pos)
        self.controller.preview_selection(())
        self._selection_window = QGraphicsRectItem()
        self._selection_window.setPen(QPen(QColor("#a95820"), 1, Qt.PenStyle.DashLine))
        self._selection_window.setBrush(QBrush(QColor(215, 123, 50, 45)))
        self._selection_window.setZValue(5)
        self._selection_window.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.scene.addItem(self._selection_window)
        self._apply_timeline_selection_preview()

    def update_timeline_selection(self, view_pos):
        if self._selection_origin is None or self._selection_window is None:
            return
        current = self.view.mapToScene(view_pos)
        rectangle = QRectF(self._selection_origin, current).normalized()
        self._selection_window.setRect(rectangle)
        selected = {
            item.segment.source_id
            for item in self.scene.items()
            if isinstance(item, SegmentItem) and rectangle.intersects(item.sceneBoundingRect())
        }
        self.controller.preview_selection(selected)
        self._apply_timeline_selection_preview()
        self.status.setText(f"{len(selected)} block{'s' if len(selected) != 1 else ''} selected")

    def finish_timeline_selection(self, view_pos):
        self.update_timeline_selection(view_pos)
        if self._selection_window is not None:
            self.scene.removeItem(self._selection_window)
        self._selection_window = None
        self._selection_origin = None

    def _apply_timeline_selection_preview(self):
        """Repaint selection without rebuilding the grabbed graphics scene."""
        selected = self.controller.state.selected
        for item in self.scene.items():
            if isinstance(item, SegmentItem):
                active = item.segment.source_id in selected
                item.setPen(QPen(QColor("#57544e") if active else QColor("#403c36"), 3 if active else 1))
        rows = {
            index for index, data in enumerate(self._table_rows)
            if data.get("id") in selected
        }
        self.apply_table_row_highlights(rows)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "editor"):
            self._layout_editor_sheet()

    def toggle_editor(self):
        visible = not self._editor_visible
        self.set_editor_visible(visible)
        if visible:
            self.render()

    def open_selected_day_editor(self):
        """Open the editor for the date currently selected in the month ledger."""
        day = self.month_overview.selected_day()
        if day is None:
            return
        self.select_day(day)
        self.set_editor_visible(True)
        self.render()

    def open_day_editor(self, day: date):
        """Select and open a ledger day in one double-click action."""
        self.select_day(day)
        self.set_editor_visible(True)
        self.render()

    def queue_day_type(self, day: date, action: str):
        # Keep the clicked cell alive until its menu event has fully returned.
        QTimer.singleShot(0, lambda: self.set_day_type(day, action))

    def set_day_type(self, day: date, action: str):
        """Changing a day flag only changes editability, never its timeline."""
        day_type = DayType(action)
        self.schedules.set_day_type(day, day_type)
        if day == self.schedules.selected_date:
            self.editor_content.setEnabled(day_type is DayType.WORKDAY)
        # This changes neither authored blocks nor the lower editor rows.
        # Rebuild only the ledger that displays credited weekly time; avoiding
        # the 24-hour graphics scene makes the state switch feel immediate.
        self.month_overview.refresh()
        if day == self.schedules.selected_date:
            self.status.setText("Workday — editing enabled" if day_type is DayType.WORKDAY else f"{day_type.value.replace('_', ' ').title()} — editing disabled")
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
            focus = QApplication.focusWidget()
            in_month_table = ((isinstance(watched, QWidget) and (watched is self.month_overview.table or self.month_overview.table.isAncestorOf(watched)))
                              or (isinstance(focus, QWidget) and (focus is self.month_overview.table or self.month_overview.table.isAncestorOf(focus))))
            in_editor_input = (isinstance(focus, (QLineEdit, QComboBox))
                               and (focus is self.editor or self.editor.isAncestorOf(focus)))
            in_timeline_side = ((isinstance(watched, QWidget) and (watched is self.timeline_panel or self.timeline_panel.isAncestorOf(watched)))
                                or (isinstance(focus, QWidget) and (focus is self.timeline_panel or self.timeline_panel.isAncestorOf(focus))))
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and in_month_table:
                return handle(self.open_selected_day_editor)
            if (self._editor_visible and in_timeline_side and not in_editor_input
                    and event.modifiers() == Qt.KeyboardModifier.NoModifier
                    and event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right)):
                step = -5 if event.key() == Qt.Key.Key_Left else 5
                return handle(lambda: self.command(
                    lambda: self.controller.set_cursor(self.controller.state.cursor_minute + step)
                ))
            if event.key() == Qt.Key.Key_Escape and in_month_table and self._editor_visible:
                return handle(lambda: self.set_editor_visible(False))
            # Escape is reserved for ending an edit while a time/type input has
            # focus.  Everywhere else in the visible editor it closes the pane.
            if event.key() == Qt.Key.Key_Escape and self._editor_visible and not in_editor_input:
                return handle(lambda: self.set_editor_visible(False))
            if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
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
        return self.controller.replace_all((), cursor_minute=720)

    def copy_previous_schedule(self):
        if self.schedules.copy_previous_eligible(self.schedules.selected_date):
            self._pending_rows.clear()
            self._table_drafts.clear()
            self.controller.message = "Copied the most recent eligible previous schedule."
        else:
            self.controller.message = "No eligible previous schedule is available to copy."
        self.render()

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
        if not self._editor_visible:
            self.month_overview.refresh()
            return
        self.scene.clear(); pen = QPen(QColor("#9b9286"))
        for minute in range(0, 1441, 5):
            x = LEFT + minute * SCALE; height = 22 if minute % 60 == 0 else (14 if minute % 15 == 0 else 7)
            self.scene.addLine(x, TOP - height, x, TOP, pen)
            if minute < 1440 and minute % 60 == 0:
                label = self.scene.addText(f"{minute // 60:02}:00"); label.setDefaultTextColor(QColor("#5d574e")); label.setPos(x - 16, TOP - 45)
        self.cursor_line, self.cursor_tooltip = self._add_cursor(self.controller.state.cursor_minute)
        self.drag_end_line = self.drag_end_tooltip = None
        for segment in self.controller.projection.segments:
            item = SegmentItem(segment, self)
            selected = segment.source_id in self.controller.state.selected
            item.setPen(QPen(QColor("#57544e") if selected else QColor("#403c36"), 3 if selected else 1))
            self.scene.addItem(item)
        self.scene.setSceneRect(0, TOP - 72, LEFT + 1440 * SCALE + 24, 135)
        self.status.setText(self.controller.message or f"Cursor: {clock(self.controller.state.cursor_minute)}")
        self.refresh_table()
        if not self._editor_visible:
            self.month_overview.refresh()
        if self._center_timeline_on_open:
            self._center_timeline_on_open = False
            # The view's scrollbar range is settled only after the event that
            # shows and lays out the bottom sheet has returned.
            QTimer.singleShot(60, self._center_timeline_for_open)

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
            start.timePreviewed.connect(lambda minute, d=data, c=combo, s=start, e=end: self.table_time_preview(d, c, s, e, "left", minute))
            end.timePreviewed.connect(lambda minute, d=data, c=combo, s=start, e=end: self.table_time_preview(d, c, s, e, "right", minute))
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
                    widget.setStyleSheet("background: #f0c28f; color: #34312c; border: 1px solid #c76d28;" if selected else "")

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
        if self._table_preview_gesture == data.get("segment_key"):
            self._table_preview_gesture = None
            self.controller.commit_gesture()
            self.defer_render()
            return
        kind = BlockKind(combo.currentData())
        changed = (self.controller.replace_visible_strict(data["segment_key"], kind, start, end)
                   if "segment_key" in data else self.controller.replace_strict(None, kind, start, end))
        if changed:
            self._table_drafts.pop(data.get("segment_key", ""), None)
            if data in self._pending_rows: self._pending_rows.remove(data)
            self.defer_render()

    def table_time_preview(self, data: dict, combo: QComboBox, start_box: TimeEdit,
                           end_box: TimeEdit, edge: str, minute: int):
        """Preview a timestamp drag in the graphics timeline without history."""
        key = data.get("segment_key")
        if not key:
            return
        start, end = parse_clock(start_box.text()), parse_clock(end_box.text())
        if start is None or end is None or start >= end:
            return
        if self._table_preview_gesture != data.get("segment_key"):
            self.controller.begin_gesture()
            self._table_preview_gesture = data.get("segment_key")
        preview_start = minute if edge == "left" else start
        preview_end = end if edge == "left" else minute
        if not self.controller.preview_replace_visible_strict(
                key, BlockKind(combo.currentData()), preview_start, preview_end):
            return
        span = self._paint_selected_drag_preview()
        if span is None:
            return
        left, right = span
        self._set_cursor_position(self.cursor_line, self.cursor_tooltip, left)
        if self.drag_end_line is None:
            self.drag_end_line, self.drag_end_tooltip = self._add_cursor(right)
        else:
            self._set_cursor_position(self.drag_end_line, self.drag_end_tooltip, right)
        selected = len(self.controller.state.selected)
        self.status.setText(f"{selected} selected: {clock(left)} – {clock(right)} ({right - left} min)")

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

    def _paint_selected_drag_preview(self) -> tuple[int, int] | None:
        """Update selected graphics items in place from the group preview."""
        selected = self.controller.state.selected
        preview_segments = [segment for segment in self.controller.projection.segments if segment.source_id in selected]
        if not preview_segments:
            return None
        preview_by_source: dict[tuple[str, BlockKind], list] = {}
        item_by_source: dict[tuple[str, BlockKind], list[SegmentItem]] = {}
        for segment in preview_segments:
            preview_by_source.setdefault((segment.source_id, segment.kind), []).append(segment)
        for graphic_item in self.scene.items():
            if isinstance(graphic_item, SegmentItem) and graphic_item.segment.source_id in selected:
                item_by_source.setdefault((graphic_item.segment.source_id, graphic_item.segment.kind), []).append(graphic_item)
        for key, graphic_items in item_by_source.items():
            segments = preview_by_source.get(key, [])
            for graphic_item, segment in zip(
                sorted(graphic_items, key=lambda current: (current.segment.start_minute, current.segment.end_minute)),
                sorted(segments, key=lambda current: (current.start_minute, current.end_minute)),
            ):
                graphic_item.set_segment_geometry(segment)
        return min(segment.start_minute for segment in preview_segments), max(segment.end_minute for segment in preview_segments)

    def preview_drag(self, item: SegmentItem, delta: int):
        """Paint an active drag immediately without replacing Qt's grabbed item."""
        self.controller.preview_move(item.segment.source_id, delta)
        span = self._paint_selected_drag_preview()
        if span is None:
            return
        start, end = span
        self._set_cursor_position(self.cursor_line, self.cursor_tooltip, start)
        if self.drag_end_line is None:
            self.drag_end_line, self.drag_end_tooltip = self._add_cursor(end)
        else:
            self._set_cursor_position(self.drag_end_line, self.drag_end_tooltip, end)
        count = len(self.controller.state.selected)
        self.status.setText(f"{count} selected: {clock(start)} – {clock(end)} ({end - start} min)")
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
                self.command(lambda: self.controller.set_cursor(self._rounded_cursor_minute(point.x())))
if __name__ == "__main__":
    application = QApplication(sys.argv); window = TimelineWindow(); window.show(); sys.exit(application.exec())
