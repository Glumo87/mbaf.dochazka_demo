"""Pure state and projection logic for the attendance timeline.

The objects in this module deliberately know nothing about Qt.  A view can be
thrown away and rebuilt at any time; IDs and selection live here instead.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Iterable
from uuid import uuid4

DAY = 24 * 60


class BlockKind(str, Enum):
    WORK = "work"
    BREAK = "break"
    VACATION = "vacation"
    DOCTOR = "doctor"


@dataclass(frozen=True)
class KindInfo:
    label: str
    color: str
    default_duration: int
    overwrite_targets: frozenset[BlockKind] = frozenset()


KINDS = {
    BlockKind.WORK: KindInfo("Work", "#4d8ed8", 480),
    BlockKind.BREAK: KindInfo("Break", "#e6a93d", 30),
    BlockKind.VACATION: KindInfo("Vacation", "#63b879", 240, frozenset({BlockKind.WORK})),
    BlockKind.DOCTOR: KindInfo("Doctor visit", "#ba72c9", 60, frozenset({BlockKind.WORK, BlockKind.BREAK, BlockKind.VACATION})),
}


@dataclass(frozen=True, order=True)
class Block:
    start_minute: int
    end_minute: int
    kind: BlockKind = field(compare=False)
    id: str = field(default_factory=lambda: uuid4().hex, compare=False)

    def __post_init__(self) -> None:
        if not 0 <= self.start_minute < self.end_minute <= DAY:
            raise ValueError("A block must be inside 00:00–24:00 and at least one minute long")

    @property
    def duration(self) -> int:
        return self.end_minute - self.start_minute


@dataclass(frozen=True)
class TimelineState:
    blocks: tuple[Block, ...] = ()
    cursor_minute: int = 720
    selected: frozenset[str] = frozenset()
    selection_anchor: str | None = None

    def __post_init__(self) -> None:
        if not 0 <= self.cursor_minute <= DAY:
            raise ValueError("Cursor must be inside the day")
        ids = [block.id for block in self.blocks]
        if len(ids) != len(set(ids)):
            raise ValueError("Block IDs must be unique")

    @classmethod
    def example(cls) -> "TimelineState":
        return cls((Block(480, 960, BlockKind.WORK),))


@dataclass(frozen=True)
class DisplaySegment:
    key: str
    source_id: str
    kind: BlockKind
    start_minute: int
    end_minute: int
    derived: bool = False


@dataclass(frozen=True)
class TimelineProjection:
    segments: tuple[DisplaySegment, ...]


def _overlap(a: int, b: int, c: int, d: int) -> bool:
    return a < d and c < b


def _masked(start: int, end: int, masks: Iterable[Block]) -> list[tuple[int, int]]:
    """Subtract mask intervals from an interval."""
    pieces = [(start, end)]
    for mask in sorted(masks):
        next_pieces: list[tuple[int, int]] = []
        for left, right in pieces:
            if not _overlap(left, right, mask.start_minute, mask.end_minute):
                next_pieces.append((left, right))
            else:
                if left < mask.start_minute:
                    next_pieces.append((left, min(right, mask.start_minute)))
                if mask.end_minute < right:
                    next_pieces.append((max(left, mask.end_minute), right))
        pieces = next_pieces
    return pieces


def project(state: TimelineState) -> TimelineProjection:
    """Build visible segments, applying doctor/vacation masks and break flow."""
    doctors = [b for b in state.blocks if b.kind is BlockKind.DOCTOR]
    vacations = [b for b in state.blocks if b.kind is BlockKind.VACATION]
    # Doctor hides vacations and breaks, so only their uncovered portions act.
    visible_vacations = [(b, p) for b in vacations for p in _masked(b.start_minute, b.end_minute, doctors)]
    breaks = [b for b in state.blocks if b.kind is BlockKind.BREAK]
    visible_breaks = [(b, p) for b in breaks for p in _masked(b.start_minute, b.end_minute, doctors)]
    segments: list[DisplaySegment] = []
    for d in doctors:
        segments.append(DisplaySegment(f"{d.id}:0", d.id, d.kind, d.start_minute, d.end_minute))
    for v, (a, b) in visible_vacations:
        segments.append(DisplaySegment(f"{v.id}:{a}-{b}", v.id, v.kind, a, b, (a, b) != (v.start_minute, v.end_minute)))
    for br, (a, b) in visible_breaks:
        segments.append(DisplaySegment(f"{br.id}:{a}-{b}", br.id, br.kind, a, b, (a, b) != (br.start_minute, br.end_minute)))

    vacation_masks = [Block(a, b, BlockKind.VACATION, v.id) for v, (a, b) in visible_vacations]
    for work in (b for b in state.blocks if b.kind is BlockKind.WORK):
        # Break insertion materialises Work pieces in the authored state.  The
        # projection therefore only masks Work; it never owns a relationship
        # between the two pieces on either side of a Break.
        for x, y in _masked(work.start_minute, work.end_minute, [*doctors, *vacation_masks]):
            segments.append(DisplaySegment(f"{work.id}:0:{x}-{y}", work.id, work.kind, x, y, (x, y) != (work.start_minute, work.end_minute)))
    return TimelineProjection(tuple(sorted(segments, key=lambda s: (s.start_minute, s.end_minute, s.key))))


def _merge(blocks: Iterable[Block]) -> tuple[Block, ...]:
    """Merge touching same-kind authored blocks, preserving the first ID."""
    result: list[Block] = []
    for block in sorted(blocks, key=lambda b: (b.kind.value, b.start_minute, b.end_minute, b.id)):
        matches = [i for i, old in enumerate(result) if old.kind is block.kind and _overlap(old.start_minute, old.end_minute + 1, block.start_minute, block.end_minute + 1)]
        if matches:
            i = matches[0]
            old = result[i]
            result[i] = Block(min(old.start_minute, block.start_minute), max(old.end_minute, block.end_minute), old.kind, old.id)
        else:
            result.append(block)
    return tuple(sorted(result))


def _split_work_for_break(work: Block, br: Block) -> list[Block]:
    """Create independent Work pieces around a Break.

    A Break that only crosses Work's left edge moves its covered Work minutes
    immediately before Work rather than shifting the entire Work interval.
    """
    ws, we, bs, be = work.start_minute, work.end_minute, br.start_minute, br.end_minute
    if bs <= ws < be < we:
        overlap = be - ws
        if ws >= overlap:
            return [Block(ws - overlap, ws, BlockKind.WORK, work.id), Block(be, we, BlockKind.WORK)]
    if ws < bs < we <= be:
        overlap = we - bs
        if ws >= overlap:
            return [Block(ws, bs, BlockKind.WORK, work.id), Block(ws - overlap, ws, BlockKind.WORK)]
    if bs <= ws and be >= we:
        if ws >= work.duration:
            return [Block(ws - work.duration, ws, BlockKind.WORK, work.id)]
        return [Block(be, be + work.duration, BlockKind.WORK, work.id)]
    pieces: list[Block] = []
    if ws < bs:
        pieces.append(replace(work, end_minute=bs))
    right_start = max(be, ws + br.duration)
    right_end = we + br.duration
    if right_start < right_end:
        pieces.append(Block(right_start, right_end, BlockKind.WORK))
    return pieces


def _split_work_for_rightward_break_drop(work: Block, br: Block, previous_break: Block) -> list[Block]:
    """Fill the path left by a rightward Break drag without extending Work."""
    pieces: list[Block] = []
    # Only reclaim the Break's old slot when it was directly adjacent to Work.
    # A Break dragged in from elsewhere must not create Work across its path.
    left_start = previous_break.start_minute if previous_break.end_minute == work.start_minute else work.start_minute
    if left_start < br.start_minute:
        pieces.append(Block(left_start, br.start_minute, BlockKind.WORK, work.id))
    if br.end_minute < work.end_minute:
        pieces.append(Block(max(work.start_minute, br.end_minute), work.end_minute, BlockKind.WORK))
    return pieces


def _merged_ranges(blocks: Iterable[Block]) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    for block in sorted(blocks):
        if not ranges or block.start_minute > ranges[-1][1]:
            ranges.append((block.start_minute, block.end_minute))
        else:
            ranges[-1] = (ranges[-1][0], max(ranges[-1][1], block.end_minute))
    return ranges


class TimelineController:
    """State owner, commands, history, and transient drag previews."""
    def __init__(self, state: TimelineState | None = None) -> None:
        self.state = state or TimelineState.example()
        self._undo: list[TimelineState] = []
        self._redo: list[TimelineState] = []
        self._gesture_start: TimelineState | None = None
        self.message = ""

    @property
    def projection(self) -> TimelineProjection:
        return project(self.state)

    @property
    def can_undo(self) -> bool: return bool(self._undo)
    @property
    def can_redo(self) -> bool: return bool(self._redo)

    @staticmethod
    def _states_equal(left: TimelineState, right: TimelineState) -> bool:
        """Compare snapshots including block kind (excluded from sort ordering)."""
        return (
            left.cursor_minute == right.cursor_minute
            and left.selected == right.selected
            and left.selection_anchor == right.selection_anchor
            and [(b.id, b.kind, b.start_minute, b.end_minute) for b in left.blocks]
            == [(b.id, b.kind, b.start_minute, b.end_minute) for b in right.blocks]
        )

    def _commit(self, next_state: TimelineState) -> bool:
        if self._states_equal(next_state, self.state):
            return False
        self._undo.append(self.state); self._redo.clear(); self.state = next_state
        return True

    def _replace_blocks(self, blocks: Iterable[Block], **changes: object) -> TimelineState:
        clean = _merge(blocks)
        selected = frozenset(i for i in self.state.selected if any(b.id == i for b in clean))
        return TimelineState(clean, changes.get("cursor_minute", self.state.cursor_minute), changes.get("selected", selected), changes.get("selection_anchor", self.state.selection_anchor))

    def set_cursor(self, minute: int) -> bool:
        return self._commit(replace(self.state, cursor_minute=max(0, min(DAY, minute)), selected=frozenset(), selection_anchor=None))

    def select(self, source_id: str, *, toggle: bool = False, range_select: bool = False) -> bool:
        ids = [s.source_id for s in self.projection.segments]
        ids = list(dict.fromkeys(ids))
        if source_id not in ids: return False
        if range_select and self.state.selection_anchor in ids:
            a, b = sorted((ids.index(self.state.selection_anchor), ids.index(source_id)))
            selected = frozenset(ids[a:b + 1]); anchor = self.state.selection_anchor
        elif toggle:
            selected = self.state.selected ^ {source_id}; anchor = self.state.selection_anchor or source_id
        else:
            selected = frozenset({source_id}); anchor = source_id
        return self._commit(replace(self.state, selected=selected, selection_anchor=anchor))

    def add(self, kind: BlockKind) -> bool:
        start, duration = self.state.cursor_minute, KINDS[kind].default_duration
        if start + duration > DAY:
            self.message = "The new block would extend past midnight."
            return False
        if kind is BlockKind.WORK:
            affected = [b for b in self.state.blocks if b.end_minute > start]
            if any(b.end_minute + duration > DAY for b in affected):
                self.message = "Work insertion would push time past midnight."
                return False
            blocks: list[Block] = []
            for b in self.state.blocks:
                if b.start_minute >= start:
                    blocks.append(replace(b, start_minute=b.start_minute + duration, end_minute=b.end_minute + duration))
                elif b.end_minute > start:
                    blocks.extend((replace(b, end_minute=start), Block(start + duration, b.end_minute + duration, b.kind)))
                else: blocks.append(b)
            blocks.append(Block(start, start + duration, kind))
        elif kind is BlockKind.BREAK:
            end = start + duration
            affected = [b for b in self.state.blocks if b.kind is BlockKind.WORK and _overlap(b.start_minute, b.end_minute, start, end)]
            fully_inside = [b for b in affected if b.start_minute < start and end < b.end_minute]
            if any(b.end_minute + duration > DAY for b in fully_inside):
                self.message = "The Break would push affected Work past midnight."
                return False
            blocks = []
            for b in self.state.blocks:
                if b.kind is not BlockKind.WORK or not _overlap(b.start_minute, b.end_minute, start, end):
                    blocks.append(b)
                    continue
                blocks.extend(_split_work_for_break(b, Block(start, end, BlockKind.BREAK)))
            blocks.append(Block(start, end, kind))
        else:
            blocks = [*self.state.blocks, Block(start, start + duration, kind)]
        self.message = ""
        return self._commit(self._replace_blocks(blocks))

    def delete(self, ids: Iterable[str] | None = None) -> bool:
        gone = set(ids if ids is not None else self.state.selected)
        return self._commit(self._replace_blocks((b for b in self.state.blocks if b.id not in gone), selected=frozenset(), selection_anchor=None))

    def replace_strict(self, source_id: str | None, kind: BlockKind, start: int, end: int) -> bool:
        """Table-style edit: replace the edited range and remove all overlaps."""
        try:
            replacement = Block(start, end, kind, source_id or uuid4().hex)
        except ValueError as error:
            self.message = str(error)
            return False
        blocks = [b for b in self.state.blocks if b.id != source_id and not _overlap(b.start_minute, b.end_minute, start, end)]
        blocks.append(replacement)
        self.message = ""
        return self._commit(self._replace_blocks(blocks, selected=frozenset({replacement.id}), selection_anchor=replacement.id))

    def replace_visible_strict(self, segment_key: str, kind: BlockKind, start: int, end: int) -> bool:
        """Strictly edit one visible table row and materialise its siblings."""
        segment = next((item for item in self.projection.segments if item.key == segment_key), None)
        if segment is None:
            self.message = "That visible row is no longer available."
            return False
        try:
            replacement = Block(start, end, kind, segment.source_id)
        except ValueError as error:
            self.message = str(error)
            return False
        siblings = [item for item in self.projection.segments if item.source_id == segment.source_id and item.key != segment_key]
        blocks = [block for block in self.state.blocks if block.id != segment.source_id and not _overlap(block.start_minute, block.end_minute, start, end)]
        for sibling in siblings:
            if not _overlap(sibling.start_minute, sibling.end_minute, start, end):
                blocks.append(Block(sibling.start_minute, sibling.end_minute, sibling.kind))
        blocks.append(replacement)
        self.message = ""
        return self._commit(self._replace_blocks(blocks, selected=frozenset({replacement.id}), selection_anchor=replacement.id))

    def change_visible_kind(self, segment_key: str, kind: BlockKind) -> bool:
        """Change one visible block type without changing other visible ranges."""
        selected = next((item for item in self.projection.segments if item.key == segment_key), None)
        source = next((item for item in self.state.blocks if selected and item.id == selected.source_id), None)
        if selected is None or source is None:
            self.message = "That visible block is no longer available."
            return False
        # Any source masked by this overlay is replaced by its currently
        # visible fragments, so removing the overlay cannot resurrect hidden
        # time. This is deliberately destructive only to the old hidden state.
        affected_ids = {source.id}
        if source.kind in (BlockKind.DOCTOR, BlockKind.VACATION):
            targets = KINDS[source.kind].overwrite_targets
            affected_ids.update(block.id for block in self.state.blocks if block.kind in targets and _overlap(block.start_minute, block.end_minute, source.start_minute, source.end_minute))
        blocks = [block for block in self.state.blocks if block.id not in affected_ids]
        seen_sources: set[str] = set()
        for segment in self.projection.segments:
            if segment.source_id not in affected_ids or segment.source_id == source.id:
                continue
            segment_id = segment.source_id if segment.source_id not in seen_sources else uuid4().hex
            seen_sources.add(segment.source_id)
            blocks.append(Block(segment.start_minute, segment.end_minute, segment.kind, segment_id))
        blocks.append(Block(selected.start_minute, selected.end_minute, kind, source.id))
        self.message = ""
        return self._commit(self._replace_blocks(blocks, selected=frozenset({source.id}), selection_anchor=source.id))

    def move(self, source_id: str, delta: int) -> bool:
        source = next((b for b in self.state.blocks if b.id == source_id), None)
        if source is not None and source.kind is BlockKind.WORK and self.state.selected <= {source_id}:
            next_state = self._move_work_with_overflow(source, delta)
            if next_state is None:
                self.message = "Not enough space to move Work past the blocking interval."
                return False
            return self._commit(next_state)
        moved = self._moved_state(source_id, delta)
        overlay_before = next((b for b in self.state.blocks if b.id == source_id), None)
        overlay_after = next((b for b in moved.blocks if b.id == source_id), None)
        moving_right = bool(overlay_before and overlay_after and overlay_after.start_minute > overlay_before.start_minute)
        return self._commit(self._settle_overlay(moved, source_id, moving_right=moving_right, previous_overlay=overlay_before))

    def _move_work_with_overflow(self, work: Block, delta: int) -> TimelineState | None:
        """Move one Work interval, routing any blocked minutes beyond blockers."""
        delta = max(-work.start_minute, min(delta, DAY - work.end_minute))
        start, end = work.start_minute + delta, work.end_minute + delta
        blockers = [b for b in self.state.blocks if b.kind in (BlockKind.BREAK, BlockKind.VACATION, BlockKind.DOCTOR)]
        ranges = _merged_ranges(blockers)
        visible = [(start, end)]
        for left, right in ranges:
            next_visible: list[tuple[int, int]] = []
            for a, b in visible:
                if not _overlap(a, b, left, right):
                    next_visible.append((a, b)); continue
                if a < left: next_visible.append((a, left))
                if right < b: next_visible.append((right, b))
            visible = next_visible
        overflow = work.duration - sum(b - a for a, b in visible)
        if not overflow:
            return self._replace_blocks((replace(b, start_minute=start, end_minute=end) if b.id == work.id else b for b in self.state.blocks), cursor_minute=start)
        crossed = [(a, b) for a, b in ranges if _overlap(start, end, a, b)]
        other_work = [b for b in self.state.blocks if b.kind is BlockKind.WORK and b.id != work.id]
        if delta < 0:
            cursor = min(a for a, _ in crossed)
            while True:
                hit = next(((a, b) for a, b in ranges if _overlap(cursor - overflow, cursor, a, b)), None)
                adjacent = next((b for b in other_work if b.end_minute == cursor), None)
                if hit is not None:
                    cursor = hit[0]
                elif adjacent is not None:
                    # Extend an existing Work block on this side outwards,
                    # rather than placing an overlapping fragment inside it.
                    cursor = adjacent.start_minute
                else:
                    break
            if cursor - overflow < 0: return None
            overflow_piece = (cursor - overflow, cursor)
        else:
            cursor = max(b for _, b in crossed)
            while True:
                hit = next(((a, b) for a, b in ranges if _overlap(cursor, cursor + overflow, a, b)), None)
                adjacent = next((b for b in other_work if b.start_minute == cursor), None)
                if hit is not None:
                    cursor = hit[1]
                elif adjacent is not None:
                    cursor = adjacent.end_minute
                else:
                    break
            if cursor + overflow > DAY: return None
            overflow_piece = (cursor, cursor + overflow)
        pieces = [*visible, overflow_piece]
        replacements = [Block(a, b, BlockKind.WORK, work.id if index == 0 else uuid4().hex) for index, (a, b) in enumerate(pieces) if a < b]
        blocks = [b for b in self.state.blocks if b.id != work.id]
        return self._replace_blocks([*blocks, *replacements], cursor_minute=min(a for a, _ in pieces))

    def _settle_overlay(self, state: TimelineState, source_id: str | None, *, moving_right: bool = False, previous_overlay: Block | None = None) -> TimelineState:
        """Materialise Work pieces when a Break or Doctor is dropped on Work."""
        overlay = next((b for b in state.blocks if b.id == source_id), None)
        if overlay is None or overlay.kind not in (BlockKind.BREAK, BlockKind.VACATION, BlockKind.DOCTOR):
            return state
        affected = [b for b in state.blocks if b.kind is BlockKind.WORK and _overlap(b.start_minute, b.end_minute, overlay.start_minute, overlay.end_minute)]
        if not affected:
            return state
        fully_inside = [b for b in affected if b.start_minute < overlay.start_minute and overlay.end_minute < b.end_minute]
        if overlay.kind is BlockKind.BREAK and any(b.end_minute + overlay.duration > DAY for b in fully_inside):
            self.message = "The Break cannot flow Work past midnight."
            return state
        blocks: list[Block] = []
        for block in state.blocks:
            if block not in affected:
                blocks.append(block)
                continue
            if overlay.kind is BlockKind.BREAK:
                if moving_right and previous_overlay is not None:
                    blocks.extend(_split_work_for_rightward_break_drop(block, overlay, previous_overlay))
                else:
                    blocks.extend(_split_work_for_break(block, overlay))
            else:
                # A Doctor visit overwrites its covered time without adding it.
                if block.start_minute < overlay.start_minute:
                    blocks.append(replace(block, end_minute=overlay.start_minute))
                if overlay.end_minute < block.end_minute:
                    blocks.append(Block(overlay.end_minute, block.end_minute, BlockKind.WORK))
        return TimelineState(_merge(blocks), state.cursor_minute, state.selected, state.selection_anchor)

    def move_segment(self, key: str, delta: int) -> bool:
        """Move a displayed fragment, materialising it before the edit if needed."""
        before = self.state
        source_id = self._materialize_segment(key)
        changed = bool(source_id) and self.move(source_id, delta)
        if changed:
            self._undo[-1] = before
        else:
            self.state = before
        return changed

    def _materialize_segment(self, key: str) -> str | None:
        segment = next((s for s in self.projection.segments if s.key == key), None)
        if segment is None:
            return None
        if not segment.derived or segment.kind is not BlockKind.WORK:
            return segment.source_id
        # The projection is the authority for a fragment's current boundaries.
        # Splitting the source into its currently visible pieces lets this one
        # become independently editable while preserving the other pieces.
        pieces = [s for s in self.projection.segments if s.source_id == segment.source_id and s.kind is BlockKind.WORK]
        blocks = [b for b in self.state.blocks if b.id != segment.source_id]
        replacements: list[Block] = []
        chosen_id: str | None = None
        for index, piece in enumerate(pieces):
            block = Block(piece.start_minute, piece.end_minute, BlockKind.WORK, segment.source_id if index == 0 else uuid4().hex)
            replacements.append(block)
            if piece.key == key:
                chosen_id = block.id
        self.state = self._replace_blocks([*blocks, *replacements], selected=frozenset({chosen_id}) if chosen_id else frozenset(), selection_anchor=chosen_id)
        return chosen_id

    def _moved_state(self, source_id: str, delta: int) -> TimelineState:
        source = next((b for b in self.state.blocks if b.id == source_id), None)
        if source is not None and source.kind is BlockKind.WORK and self.state.selected <= {source_id}:
            moved = self._move_work_with_overflow(source, delta)
            if moved is None:
                self.message = "Not enough space to move Work past the blocking interval."
                return self.state
            return moved
        ids = self.state.selected if source_id in self.state.selected else frozenset({source_id})
        chosen = [b for b in self.state.blocks if b.id in ids]
        if not chosen: return self.state
        delta = max(-min(b.start_minute for b in chosen), min(delta, DAY - max(b.end_minute for b in chosen)))
        moved = [replace(b, start_minute=b.start_minute + delta, end_minute=b.end_minute + delta) if b.id in ids else b for b in self.state.blocks]
        # During a drag the insertion cursor follows the leading selected block.
        cursor = min(b.start_minute for b in moved if b.id in ids)
        if len(ids) > 1:
            moved_group = [b for b in moved if b.id in ids]
            # A group drag overwrites only the intersecting portion.  Preserve
            # any left/right fragments, deleting a block only if fully covered.
            overwritten: list[Block] = [b for b in moved if b.id in ids]
            for block in (b for b in moved if b.id not in ids):
                fragments = _masked(block.start_minute, block.end_minute, moved_group)
                for index, (start, end) in enumerate(fragments):
                    overwritten.append(Block(start, end, block.kind, block.id if index == 0 else uuid4().hex))
            moved = overwritten
        return self._replace_blocks(moved, cursor_minute=cursor)

    def resize(self, source_id: str, edge: str, minute: int) -> bool:
        return self._commit(self._resized_state(source_id, edge, minute))

    def _resized_state(self, source_id: str, edge: str, minute: int) -> TimelineState:
        block = next((b for b in self.state.blocks if b.id == source_id), None)
        if not block: return self.state
        minute = max(0, min(DAY, minute))
        # Adjacency is computed just for this edit; no neighbour links are kept.
        neighbour = next((b for b in self.state.blocks if b.id != block.id and ((edge == "left" and b.end_minute == block.start_minute) or (edge == "right" and b.start_minute == block.end_minute))), None)
        if edge == "left":
            lower = neighbour.start_minute + 1 if neighbour else 0
            boundary = min(max(minute, lower), block.end_minute - 1)
            new, other = replace(block, start_minute=boundary), (replace(neighbour, end_minute=boundary) if neighbour else None)
        else:
            upper = neighbour.end_minute - 1 if neighbour else DAY
            boundary = max(min(minute, upper), block.start_minute + 1)
            new, other = replace(block, end_minute=boundary), (replace(neighbour, start_minute=boundary) if neighbour else None)
        if other is not None:
            if other.duration < 1: return self.state
            updated = (new if b.id == source_id else other if b.id == other.id else b for b in self.state.blocks)
        else:
            updated = (new if b.id == source_id else b for b in self.state.blocks)
        # The insertion cursor stays meaningful while the left boundary moves.
        cursor = new.start_minute
        return self._replace_blocks(updated, cursor_minute=cursor)

    def resize_segment(self, key: str, edge: str, minute: int) -> bool:
        """Resize a displayed fragment, materialising a derived Work fragment."""
        before = self.state
        source_id = self._materialize_segment(key)
        changed = bool(source_id) and self.resize(source_id, edge, minute)
        if changed:
            self._undo[-1] = before
        else:
            self.state = before
        return changed

    def _materialize_visible_segment(self, key: str) -> str | None:
        """Replace one projected source with its visible authored pieces."""
        segment = next((item for item in self.projection.segments if item.key == key), None)
        if segment is None:
            return None
        pieces = [item for item in self.projection.segments if item.source_id == segment.source_id]
        blocks = [block for block in self.state.blocks if block.id != segment.source_id]
        chosen_id: str | None = None
        for index, piece in enumerate(pieces):
            block = Block(piece.start_minute, piece.end_minute, piece.kind, segment.source_id if piece.key == key else uuid4().hex)
            blocks.append(block)
            if piece.key == key: chosen_id = block.id
        self.state = self._replace_blocks(blocks, selected=frozenset({chosen_id}) if chosen_id else frozenset(), selection_anchor=chosen_id)
        return chosen_id

    def close_selected_gaps(self) -> bool:
        selected = sorted((b for b in self.state.blocks if b.id in self.state.selected))
        if not selected and self.state.blocks:
            selected = sorted(self.state.blocks)
        selected_ids = {block.id for block in selected}
        if len(selected) < 2: return False
        all_blocks = sorted(self.state.blocks)
        first, last = selected[0], selected[-1]
        if any(b.id not in selected_ids and first.start_minute <= b.start_minute <= last.start_minute for b in all_blocks): return False
        cursor = selected[0].end_minute; updates = {selected[0].id: selected[0]}
        for b in selected[1:]:
            updates[b.id] = replace(b, start_minute=cursor, end_minute=cursor + b.duration); cursor += b.duration
        return self._commit(self._replace_blocks((updates.get(b.id, b) for b in self.state.blocks)))

    def fill_gap(self, source_id: str, direction: str) -> bool:
        """Extend one authored block through the empty gap to its next neighbour."""
        block = next((item for item in self.state.blocks if item.id == source_id), None)
        if block is None: return False
        others = [item for item in self.state.blocks if item.id != source_id]
        if direction == "left":
            boundary = max((item.end_minute for item in others if item.end_minute <= block.start_minute), default=0)
            if boundary == block.start_minute: return False
            next_state = self._resized_state(source_id, "left", boundary)
        else:
            boundary = min((item.start_minute for item in others if item.start_minute >= block.end_minute), default=DAY)
            if boundary == block.end_minute: return False
            next_state = self._resized_state(source_id, "right", boundary)
        return self._commit(next_state)

    def reset(self) -> bool: return self._commit(TimelineState.example())
    def undo(self) -> bool:
        if not self._undo: return False
        self._redo.append(self.state); self.state = self._undo.pop(); return True
    def redo(self) -> bool:
        if not self._redo: return False
        self._undo.append(self.state); self.state = self._redo.pop(); return True

    def begin_gesture(self) -> None: self._gesture_start = self.state
    def preview_move(self, source_id: str, delta: int) -> bool:
        if self._gesture_start is None: self.begin_gesture()
        self.state = self._gesture_start
        preview = self._moved_state(source_id, delta)
        self.state = preview
        return preview != self._gesture_start
    def preview_resize(self, source_id: str, edge: str, minute: int) -> bool:
        """Show a resize without creating a history entry."""
        if self._gesture_start is None: self.begin_gesture()
        self.state = self._gesture_start
        preview = self._resized_state(source_id, edge, minute)
        self.state = preview
        return preview != self._gesture_start
    def preview_resize_visible(self, key: str, edge: str, minute: int) -> bool:
        """Preview a visible-boundary resize, materialising it transactionally."""
        if self._gesture_start is None: self.begin_gesture()
        self.state = self._gesture_start
        source_id = self._materialize_visible_segment(key)
        if source_id is None:
            self.state = self._gesture_start
            return False
        preview = self._resized_state(source_id, edge, minute)
        self.state = preview
        return preview != self._gesture_start
    def cancel_gesture(self) -> None:
        if self._gesture_start is not None: self.state = self._gesture_start
        self._gesture_start = None
    def commit_gesture(self, settle_source_id: str | None = None) -> bool:
        if self._gesture_start is None: return False
        start = self._gesture_start; self._gesture_start = None
        before = next((b for b in start.blocks if b.id == settle_source_id), None)
        after = next((b for b in self.state.blocks if b.id == settle_source_id), None)
        moving_right = bool(before and after and after.start_minute > before.start_minute)
        self.state = self._settle_overlay(self.state, settle_source_id, moving_right=moving_right, previous_overlay=before)
        if self._states_equal(start, self.state): return False
        self._undo.append(start); self._redo.clear(); return True
