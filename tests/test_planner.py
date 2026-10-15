from fmvbot.planner import Move, components, merge_yield, plan_group


def board(rows: list[str], with_others: bool = False):
    """'A' = item, '.' = free, 'o' = some other movable item, '#' = blocked."""
    items, free, others = set(), set(), set()
    for r, line in enumerate(rows):
        for c, ch in enumerate(line):
            if ch == "A":
                items.add((c, r))
            elif ch == ".":
                free.add((c, r))
            elif ch == "o":
                others.add((c, r))
    return (items, free, others) if with_others else (items, free)


def simulate(items, free, plan, others=()):
    """Apply a plan and return the size of the group the final drop merges."""
    items, free, others = set(items), set(free), set(others)
    for m in plan.moves[:-1]:
        assert not m.merge
        assert m.src in items
        if m.displace:
            assert m.dst in others and free
            others.remove(m.dst)
            landing = min(free, key=lambda f: abs(f[0] - m.dst[0]) + abs(f[1] - m.dst[1]))
            free.remove(landing)
            others.add(landing)
        else:
            assert m.dst in free
            free.remove(m.dst)
        items.remove(m.src)
        items.add(m.dst)
        free.add(m.src)
    last = plan.moves[-1]
    assert last.merge and last.src in items and last.dst in items
    items.remove(last.src)
    group = next(c for c in components(items) if last.dst in c)
    return len(group) + 1


def test_merge_yield_matches_wiki():
    assert merge_yield(3) == 1
    assert merge_yield(5) == 2
    assert merge_yield(6) == 2
    assert merge_yield(10) == 4
    assert merge_yield(11) == 4
    assert merge_yield(4) == 1


def test_three_in_a_row_is_one_drag():
    items, free = board(["AAA.", "...."])
    plan = plan_group("x", items, 3, free)
    assert plan.cost == 1
    assert simulate(items, free, plan) == 3


def test_two_together_plus_one_far_drops_far_one():
    items, free = board(["AA....A"])
    plan = plan_group("x", items, 3, free)
    assert plan.moves == [Move((6, 0), (1, 0), merge=True)]


def test_scattered_five_gathers_without_accidental_joins():
    items, free = board(
        [
            "A...A",
            ".....",
            "..A..",
            ".....",
            "A...A",
        ]
    )
    plan = plan_group("x", items, 5, free)
    assert plan is not None
    assert simulate(items, free, plan) == 5


def test_six_connected_peels_one_off_for_a_merge5():
    items, free = board(["AAAAAA", "......", "......"])
    plan = plan_group("x", items, 5, free)
    assert plan is not None
    assert simulate(items, free, plan) == 5


def test_no_room_means_no_plan():
    items, free = board(["A#A#A"])
    assert plan_group("x", items, 3, free) is None


def test_too_few_items():
    items, free = board(["AA..."])
    assert plan_group("x", items, 3, free) is None


def test_six_scattered_merges_exactly_five():
    items, free = board(
        [
            "A.A.A",
            ".....",
            "A.A.A",
        ]
    )
    plan = plan_group("x", items, 5, free)
    assert plan is not None
    assert simulate(items, free, plan) == 5


def test_crowded_board_pushes_other_items_aside():
    items, free, others = board(
        [
            "AoA",
            "ooo",
            "Ao.",
        ],
        with_others=True,
    )
    plan = plan_group("x", items, 3, free, pushable=others)
    assert plan is not None
    assert any(m.displace for m in plan.moves)
    assert simulate(items, free, plan, others) == 3


def test_no_pushing_without_somewhere_for_it_to_go():
    items, free, others = board(["AoA", "ooo", "Aoo"], with_others=True)
    assert plan_group("x", items, 3, free, pushable=others) is None


def test_oversize_group_merges_whole_when_it_cannot_be_trimmed():
    items, free = board(["AAAAAA", "######"])
    assert plan_group("x", items, 5, free) is None
    plan = plan_group("x", items, 5, free, allow_oversize=True)
    assert plan.cost == 1 and plan.size == 6


def test_frozen_copies_never_join_the_merge():
    items, free = board(
        [
            "AAA.",
            "....",
            "A...",
        ]
    )
    frozen = {(2, 0)}
    plan = plan_group("x", items - frozen, 3, free, frozen=frozen)
    assert plan is not None
    moved = set(items - frozen)
    for m in plan.moves[:-1]:
        moved.discard(m.src)
        moved.add(m.dst)
    last = plan.moves[-1]
    moved.discard(last.src)
    group = next(c for c in components(moved | frozen) if last.dst in c)
    assert not group & frozen and len(group) + 1 == 3
