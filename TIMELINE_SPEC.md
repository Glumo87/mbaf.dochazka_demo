# Attendance timeline specification

## Purpose and scope

The attendance timeline is an interactive editor for one employee's single
calendar day. It is a day-level component, not a weekly planner or a payroll
calculation screen. It records time intervals with minute precision and lets a
user create, select, move, resize, and delete them.

The timeline covers the complete day, from `00:00` to `24:00`. An interval is
represented by a start minute and an end minute; its end is exclusive. Every
interval must have a duration of at least one minute and must remain within the
day.

## Display

- Show one horizontal timeline lane, with a ruler for all 24 hours.
- The ruler has hour labels, 15-minute grid lines, and 5-minute ticks. Editing
  itself is still precise to one minute.
- Each interval displays its type and its `HH:MM – HH:MM` range.
- Show a red insertion cursor and its time. Clicking empty timeline space
  moves the cursor and clears the selection.
- While an interval is being edited, show both its start and end cursor times
  and update a status message with its type, range, and duration.
- Intervals may visually overlap in the same lane when the business rules do
  not define a replacement or flow behaviour for that combination.

## Interval types

| Type | Default duration | Creation behaviour | Replaces when overlapping |
| --- | ---: | --- | --- |
| Work activity | 8 hours | Makes room at the cursor by pushing existing time later | Never replaces another type |
| Break | 30 minutes | Created at the cursor | Does not replace a type; it splits/flows affected work |
| Vacation (half day) | 4 hours | Created at the cursor | Work activity only |
| Doctor visit | 1 hour | Created at the cursor | Work activity, Break, and Vacation |

The type, label, default duration, and colour are configuration data. The
component must allow adding types with the same rules model later.

## Creation rules

### Insertion cursor

All Create actions use the current cursor time. A new interval cannot extend
beyond `24:00`.

### Work activity: make room

Creating Work inserts an eight-hour range at the cursor. Existing intervals
that begin at or after the cursor move later by eight hours. If the cursor lies
inside an existing interval, split it at the cursor and move the right piece
later by eight hours. The inserted Work occupies the newly created space.

Reject the action, without changing the timeline, when making room would push
any affected interval past `24:00`.

### Break: flow work around it

Creating a Break adds a 30-minute Break at the cursor. If it overlaps a Work
interval, Work is split around the Break and the Work that follows it flows to
the Break's end. This preserves the original amount of Work; the Break adds
time rather than subtracting it from Work.

If a Break is moved or resized while it overlaps Work, apply the same rule to
the currently affected Work. Moving the Break away restores Work to the
uninterrupted range represented by its current relationship. A manually edited
Work fragment is no longer controlled by a Break flow.

### Vacation and Doctor visit: overwrite

Vacation and Doctor visit are overlays. On creation, and when later moved or
resized, remove the part of each target interval that they cover. Preserve any
uncovered left and right pieces of the target interval. Vacation targets only
Work; Doctor visit targets Work, Break, and Vacation.

Intervals of other combinations are allowed to overlap unchanged.

## Editing interactions

- Drag an interval body to move it while keeping its duration.
- Drag the left or right edge to resize it. The opposite edge stays fixed.
- Clamp moving and resizing to the day boundary and enforce a one-minute
  minimum duration.
- Right-button dragging is fine adjustment: pointer travel maps to one timeline
  minute for every five raw minutes.
- A right click without a drag opens a context menu with **Delete block**.
- Selecting an interval gives it a visible selected state.
- `Ctrl`-click toggles an interval in the selection.
- `Shift`-click selects the inclusive chronological range between the selection
  anchor and the clicked interval.
- When two or more intervals are selected, drag a selected interval body to
  move the whole selection together, preserving all relative times. Clamp the
  group at `00:00` and `24:00`.
- When resizing an edge with a selected neighbour on that side, resize their
  shared boundary rather than their outer bounds. Both intervals must retain at
  least one minute.
- Adjacent or overlapping intervals of the same type merge into one interval
  after editing or gap closing.

## Selection command

**Close selected gaps** is available only if at least two selected intervals
form one continuous chronological selection (no unselected interval lies
between the first and last selected interval). It moves each later selected
interval left until it starts at the preceding selected interval's end,
preserving every interval's duration. It then applies same-type merging.

## Delete, reset, and history

- `Delete` and `Backspace` delete all selected intervals.
- The context-menu delete deletes its interval.
- **Reset example** replaces all intervals with one Work activity from `08:00`
  to `16:00`, places the cursor at `12:00`, and centres that time in view.
- Every state-changing add, move, resize, delete, selection change, and gap
  close operation is undoable.
- **Undo**, `Ctrl+Z` restore the state before the last operation.
- **Redo**, `Ctrl+Y`, and `Ctrl+Shift+Z` reapply the next undone operation.
- A new operation after Undo discards the redo branch. Undo and Redo controls
  are disabled when unavailable.

## Initial state and controls

The initial demonstration state contains Work activity from `08:00` to
`16:00`; the insertion cursor starts at `12:00`.

Provide controls for: Insert Work (8 h), Insert Vacation (4 h), Insert Break
(30 min), Insert Doctor visit (1 h), Close selected gaps, Undo, Redo, and
Reset example.

## Acceptance criteria

1. A user can place the cursor at any minute of the day and create each of the
   four interval types at its default duration.
2. Work insertion inside an existing interval splits and shifts its right part;
   an insertion that would exceed midnight makes no changes and explains why.
3. A Break inside Work leaves visible Work before and after the Break, with the
   Work duration preserved.
4. Vacation removes only overlapped Work; Doctor visit removes only overlapped
   Work, Break, and Vacation, preserving uncovered fragments.
5. Intervals can be moved and resized to minute precision without leaving the
   `00:00–24:00` range or becoming zero length.
6. Multi-select, group move, paired-boundary resize, gap closing, deletion,
   keyboard shortcuts, and undo/redo work as described above.
