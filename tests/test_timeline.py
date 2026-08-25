from dochazka.timeline import Block, BlockKind, TimelineController, TimelineState, project

def test_default_and_cursor_creation():
    c = TimelineController(TimelineState((), 60))
    assert c.add(BlockKind.BREAK)
    assert c.state.blocks[0].start_minute == 60
    assert c.state.blocks[0].duration == 30

def test_work_insertion_splits_and_rejects_midnight():
    c = TimelineController(TimelineState((Block(400, 800, BlockKind.WORK),), 600))
    assert c.add(BlockKind.WORK)
    # The split pieces touch the inserted Work, so committed edits merge them.
    assert sorted((b.start_minute, b.end_minute) for b in c.state.blocks) == [(400, 1280)]
    c = TimelineController(TimelineState((Block(1000, 1200, BlockKind.BREAK),), 900))
    assert not c.add(BlockKind.WORK)

def test_break_splits_work_into_independent_authored_blocks():
    work = Block(480, 960, BlockKind.WORK)
    c = TimelineController(TimelineState((work,), 600))
    assert c.add(BlockKind.BREAK)
    pieces = sorted((b for b in c.state.blocks if b.kind is BlockKind.WORK))
    assert [(b.start_minute, b.end_minute) for b in pieces] == [(480, 600), (630, 990)]
    assert pieces[0].id != pieces[1].id
    assert c.move(pieces[1].id, 10)
    assert sorted((b.start_minute, b.end_minute) for b in c.state.blocks if b.kind is BlockKind.WORK) == [(480, 600), (640, 1000)]

def test_undo_and_group_move():
    a, b = Block(100, 120, BlockKind.BREAK), Block(200, 220, BlockKind.DOCTOR)
    c = TimelineController(TimelineState((a, b), selected=frozenset({a.id, b.id})))
    c.move(a.id, -150)
    assert min(x.start_minute for x in c.state.blocks) == 0
    assert c.undo() and min(x.start_minute for x in c.state.blocks) == 100

def test_dropping_break_or_doctor_splits_work():
    work = Block(480, 960, BlockKind.WORK)
    br = Block(100, 130, BlockKind.BREAK)
    c = TimelineController(TimelineState((work, br)))
    assert c.move(br.id, 500)
    assert [(b.start_minute, b.end_minute) for b in c.state.blocks if b.kind is BlockKind.WORK] == [(480, 600), (630, 960)]
    doctor = Block(100, 160, BlockKind.DOCTOR)
    c = TimelineController(TimelineState((Block(480, 960, BlockKind.WORK), doctor)))
    assert c.move(doctor.id, 500)
    assert [(b.start_minute, b.end_minute) for b in c.state.blocks if b.kind is BlockKind.WORK] == [(480, 600), (660, 960)]

def test_break_partially_crossing_work_left_edge_moves_overlap_left():
    work = Block(480, 960, BlockKind.WORK)
    br = Block(450, 480, BlockKind.BREAK)
    c = TimelineController(TimelineState((work, br)))
    assert c.move(br.id, 15)  # Break is now 07:45–08:15.
    assert [(b.start_minute, b.end_minute) for b in c.state.blocks if b.kind is BlockKind.WORK] == [(450, 465), (495, 960)]

def test_rightward_break_drop_keeps_work_right_bound():
    br = Block(600, 630, BlockKind.BREAK)
    work = Block(630, 990, BlockKind.WORK)
    c = TimelineController(TimelineState((br, work)))
    assert c.move(br.id, 10)
    assert [(b.start_minute, b.end_minute) for b in c.state.blocks if b.kind is BlockKind.WORK] == [(600, 610), (640, 990)]

def test_rightward_break_drag_fills_its_previous_path_with_work():
    br = Block(750, 780, BlockKind.BREAK)  # 12:30–13:00
    work = Block(780, 960, BlockKind.WORK)  # 13:00–16:00
    c = TimelineController(TimelineState((br, work)))
    assert c.move(br.id, 60)
    assert [(b.start_minute, b.end_minute) for b in c.state.blocks if b.kind is BlockKind.WORK] == [(750, 810), (840, 960)]

def test_work_move_overflows_through_break_and_skips_vacation():
    br = Block(720, 750, BlockKind.BREAK)
    work = Block(750, 780, BlockKind.WORK)
    c = TimelineController(TimelineState((br, work)))
    assert c.move(work.id, -15)
    assert [(b.start_minute, b.end_minute) for b in c.state.blocks if b.kind is BlockKind.WORK] == [(705, 720), (750, 765)]
    vacation = Block(675, 720, BlockKind.VACATION)
    c = TimelineController(TimelineState((vacation, br, work)))
    assert c.move(work.id, -15)
    assert [(b.start_minute, b.end_minute) for b in c.state.blocks if b.kind is BlockKind.WORK] == [(660, 675), (750, 765)]

def test_work_overflow_extends_adjacent_work_instead_of_disappearing():
    left = Block(720, 750, BlockKind.WORK)
    br = Block(750, 780, BlockKind.BREAK)
    right = Block(780, 960, BlockKind.WORK)
    c = TimelineController(TimelineState((left, br, right)))
    assert c.move(left.id, 15)
    assert [(b.start_minute, b.end_minute) for b in c.state.blocks if b.kind is BlockKind.WORK] == [(735, 750), (780, 975)]

def test_resizing_a_touching_boundary_moves_both_blocks():
    left = Block(480, 600, BlockKind.WORK)
    right = Block(600, 660, BlockKind.BREAK)
    c = TimelineController(TimelineState((left, right)))
    assert c.resize(left.id, "right", 620)
    result = {b.kind: (b.start_minute, b.end_minute) for b in c.state.blocks}
    assert result[BlockKind.WORK] == (480, 620)
    assert result[BlockKind.BREAK] == (620, 660)

def test_strict_table_edit_overwrites_overlaps_and_merges_work():
    work = Block(360, 420, BlockKind.WORK)
    br = Block(420, 450, BlockKind.BREAK)
    c = TimelineController(TimelineState((work, br)))
    assert c.replace_strict(work.id, BlockKind.WORK, 360, 480)
    assert [(b.kind, b.start_minute, b.end_minute) for b in c.state.blocks] == [(BlockKind.WORK, 360, 480)]
    later = Block(600, 660, BlockKind.WORK)
    c = TimelineController(TimelineState((Block(480, 540, BlockKind.WORK), later)))
    assert c.replace_strict(c.state.blocks[0].id, BlockKind.WORK, 480, 600)
    assert [(b.start_minute, b.end_minute) for b in c.state.blocks] == [(480, 660)]

def test_visible_table_edit_materialises_masked_work_fragments():
    work = Block(480, 960, BlockKind.WORK)
    vacation = Block(600, 720, BlockKind.VACATION)
    c = TimelineController(TimelineState((work, vacation)))
    visible_work = [s for s in c.projection.segments if s.kind is BlockKind.WORK]
    assert [(s.start_minute, s.end_minute) for s in visible_work] == [(480, 600), (720, 960)]
    assert c.replace_visible_strict(visible_work[0].key, BlockKind.WORK, 480, 590)
    assert [(b.kind, b.start_minute, b.end_minute) for b in c.state.blocks] == [
        (BlockKind.WORK, 480, 590), (BlockKind.VACATION, 600, 720), (BlockKind.WORK, 720, 960)
    ]

def test_close_gaps_without_selection_packs_all_after_first_block():
    blocks = (Block(100, 120, BlockKind.WORK), Block(180, 200, BlockKind.BREAK), Block(260, 280, BlockKind.DOCTOR))
    c = TimelineController(TimelineState(blocks))
    assert c.close_selected_gaps()
    assert [(b.start_minute, b.end_minute) for b in c.state.blocks] == [(100, 120), (120, 140), (140, 160)]

def test_resizing_visible_work_vacation_boundary_materialises_work():
    c = TimelineController(TimelineState((Block(480, 960, BlockKind.WORK), Block(600, 720, BlockKind.VACATION))))
    key = next(s.key for s in c.projection.segments if s.kind is BlockKind.WORK and s.end_minute == 600)
    c.begin_gesture()
    assert c.preview_resize_visible(key, "right", 610)
    assert c.commit_gesture()
    assert [(b.kind, b.start_minute, b.end_minute) for b in c.state.blocks] == [
        (BlockKind.WORK, 480, 610), (BlockKind.VACATION, 610, 720), (BlockKind.WORK, 720, 960)
    ]

def test_dropping_vacation_destructively_splits_work():
    vacation = Block(100, 160, BlockKind.VACATION)
    c = TimelineController(TimelineState((Block(480, 960, BlockKind.WORK), vacation)))
    assert c.move(vacation.id, 500)
    assert [(b.kind, b.start_minute, b.end_minute) for b in c.state.blocks] == [
        (BlockKind.WORK, 480, 600), (BlockKind.VACATION, 600, 660), (BlockKind.WORK, 660, 960)
    ]

def test_fill_gap_extends_selected_block_to_nearest_boundary():
    left = Block(480, 540, BlockKind.WORK)
    right = Block(600, 660, BlockKind.BREAK)
    c = TimelineController(TimelineState((left, right)))
    assert c.fill_gap(left.id, "right")
    assert [(b.start_minute, b.end_minute) for b in c.state.blocks if b.kind is BlockKind.WORK] == [(480, 600)]
    c = TimelineController(TimelineState((left, right)))
    assert c.fill_gap(right.id, "left")
    assert [(b.start_minute, b.end_minute) for b in c.state.blocks if b.kind is BlockKind.BREAK] == [(540, 660)]

def test_context_type_change_preserves_visible_work_around_doctor():
    work = Block(480, 960, BlockKind.WORK)
    doctor = Block(600, 660, BlockKind.DOCTOR)
    c = TimelineController(TimelineState((work, doctor)))
    doctor_key = next(s.key for s in c.projection.segments if s.source_id == doctor.id)
    assert c.change_visible_kind(doctor_key, BlockKind.BREAK)
    assert [(s.kind, s.start_minute, s.end_minute) for s in c.projection.segments] == [
        (BlockKind.WORK, 480, 600), (BlockKind.BREAK, 600, 660), (BlockKind.WORK, 660, 960)
    ]

def test_kind_change_commits_when_the_block_is_already_selected():
    c = TimelineController()
    key = c.projection.segments[0].key
    c.select(c.state.blocks[0].id)
    assert c.change_visible_kind(key, BlockKind.DOCTOR)
    assert c.state.blocks[0].kind is BlockKind.DOCTOR

def test_group_move_destructively_overwrites_unselected_blocks():
    first = Block(100, 120, BlockKind.WORK)
    second = Block(140, 160, BlockKind.BREAK)
    covered = Block(200, 240, BlockKind.DOCTOR)
    c = TimelineController(TimelineState((first, second, covered), selected=frozenset({first.id, second.id})))
    assert c.move(first.id, 70)
    assert {(b.kind, b.start_minute, b.end_minute) for b in c.state.blocks} == {
        (BlockKind.WORK, 170, 190), (BlockKind.BREAK, 210, 230),
        (BlockKind.DOCTOR, 200, 210), (BlockKind.DOCTOR, 230, 240)
    }
