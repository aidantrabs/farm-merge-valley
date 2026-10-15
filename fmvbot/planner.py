"""Merge planning.

Game rules this relies on (checked against the live game):
- dropping an item onto an identical item merges the dropped item with the whole
  4-connected group of identical items under the drop point
- 3 items make 1 of the next tier, 5 make 2; bigger groups are split into 5s and 3s
- dropping an item onto an empty cell only moves it
- dropping an item onto a different, displaceable item takes its cell and pushes
  that item to a nearby empty cell
"""

from dataclasses import dataclass, field

from .game import Cell, neighbours


@dataclass(frozen=True)
class Move:
    src: Cell
    dst: Cell
    merge: bool = False
    displace: bool = False


@dataclass
class Plan:
    item_id: str
    size: int
    moves: list[Move] = field(default_factory=list)

    @property
    def cost(self) -> int:
        # pushing other items around is riskier than moving into empty cells
        return len(self.moves) + sum(m.displace for m in self.moves)

    @property
    def distance(self) -> int:
        """Total drag length in cells; long drags may need the camera to move."""
        return sum(_dist(m.src, m.dst) for m in self.moves)


def components(cells: set[Cell]) -> list[set[Cell]]:
    left = set(cells)
    out = []
    while left:
        start = left.pop()
        comp = {start}
        stack = [start]
        while stack:
            for n in neighbours(stack.pop()):
                if n in left:
                    left.remove(n)
                    comp.add(n)
                    stack.append(n)
        out.append(comp)
    return out


def merge_yield(n: int) -> int:
    """Items produced by merging a group of n (3 -> 1, 5 -> 2, larger groups split)."""
    best = (n, 0)
    for fives in range(n // 5 + 1):
        threes = (n - 5 * fives) // 3
        left = n - 5 * fives - 3 * threes
        out = 2 * fives + threes
        if (left, -out) < (best[0], -best[1]):
            best = (left, out)
    return best[1]


def _dist(a: Cell, b: Cell) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def _stays_connected(comp: set[Cell], removed: Cell) -> bool:
    rest = comp - {removed}
    return len(rest) <= 1 or len(components(rest)) == 1


def _direct(item_id: str, group: set[Cell]) -> Plan | None:
    """Drop one member of a group onto a neighbour: the whole group merges."""
    leaf = next((g for g in sorted(group) if _stays_connected(group, g)), None)
    if leaf is None:
        return None
    target = next((n for n in neighbours(leaf) if n in group), None)
    if target is None:
        return None
    return Plan(item_id, len(group), [Move(leaf, target, merge=True)])


def plan_group(
    item_id: str,
    cells: set[Cell],
    k: int,
    free: set[Cell],
    max_moves: int = 6,
    pushable: set[Cell] | None = None,
    allow_oversize: bool = False,
    frozen: set[Cell] | None = None,
    max_drag: int = 9,
) -> Plan | None:
    """Plan moves so exactly k copies of item_id end up merged.

    cells: where the movable copies are.  free: empty cells.  pushable: cells holding
    other items that may be pushed aside.  allow_oversize: if a group is bigger than k
    and can't be trimmed, merge it whole.  frozen: copies that must not be merged
    (and so must not touch the group).
    """
    frozen = set(frozen or ()) - set(cells)
    if len(cells) < k:
        return None
    pushable = pushable or set()
    best: Plan | None = None
    anchors = [c for c in components(set(cells) | frozen) if not c & frozen]
    for anchor in sorted(anchors, key=lambda c: (-min(len(c), k), len(c), min(c))):
        plan = _plan_from_anchor(item_id, cells, anchor, k, free, pushable, max_moves, frozen)
        if plan is None and allow_oversize and len(anchor) > k:
            plan = _direct(item_id, anchor)
        if plan and any(_dist(m.src, m.dst) > max_drag for m in plan.moves):
            plan = None
        if plan and (best is None or (plan.cost, plan.distance) < (best.cost, best.distance)):
            best = plan
    return best


def _plan_from_anchor(item_id, cells, anchor, k, free, pushable, max_moves, frozen=frozenset()):
    cells = set(cells) | frozen
    free = set(free)
    pushable = set(pushable) - cells
    group = set(anchor)
    moves: list[Move] = []

    def safe_spot(spot: Cell, joining: set[Cell]) -> bool:
        # a cell is safe if the only identical neighbours it touches are in `joining`
        return all(n not in cells or n in joining for n in neighbours(spot))

    def occupy(src: Cell, dst: Cell, displace: bool):
        moves.append(Move(src, dst, displace=displace))
        cells.discard(src)
        cells.add(dst)
        free.add(src)
        if displace:
            # the pushed item lands on the nearest empty cell
            pushable.discard(dst)
            landing = min(free - {src}, key=lambda f: _dist(f, dst), default=src)
            free.discard(landing)
            pushable.add(landing)
        else:
            free.discard(dst)

    # too big: peel items off the edge until k remain
    while len(group) > k:
        peel = None
        for g in sorted(group, key=lambda g: (sum(n in group for n in neighbours(g)), g)):
            if not _stays_connected(group, g):
                continue
            spots = [f for f in free if safe_spot(f, set()) and f not in neighbours(g)]
            if spots:
                peel = (g, min(spots, key=lambda f: (_dist(f, g), f)))
                break
        if peel is None:
            return None
        g, spot = peel
        occupy(g, spot, displace=False)
        group.discard(g)
        if len(moves) > max_moves:
            return None

    if len(group) == k:
        plan = _direct(item_id, group)
        if plan is None:
            return None
        moves.extend(plan.moves)
        return Plan(item_id, k, moves) if len(moves) <= max_moves else None

    # grow the group to k-1 with the nearest outside copies, then drop one more on it
    while len(group) < k - 1:
        outside = sorted(
            (c for c in cells if c not in group and c not in frozen), key=lambda c: (min(_dist(c, g) for g in group), c)
        )
        border = {n for g in group for n in neighbours(g)}
        choice = None
        for src in outside:
            joining = group | {src}
            empty_spots = [f for f in border & free if safe_spot(f, joining)]
            if empty_spots:
                choice = (src, min(empty_spots, key=lambda f: (_dist(f, src), f)), False)
                break
            # needs somewhere for the pushed item to go
            if len(free) >= 1:
                push_spots = [f for f in border & pushable if safe_spot(f, joining)]
                if push_spots:
                    choice = (src, min(push_spots, key=lambda f: (_dist(f, src), f)), True)
                    break
        if choice is None:
            return None
        src, dst, displace = choice
        occupy(src, dst, displace)
        group.add(dst)
        if len(moves) > max_moves:
            return None

    outside = [c for c in cells if c not in group and c not in frozen]
    if not outside:
        return None
    dropper = min(outside, key=lambda c: (min(_dist(c, g) for g in group), c))
    target = min(group, key=lambda g: (_dist(g, dropper), g))
    # the group under the drop point must be exactly `group`
    if any(n in cells and n not in group and n != dropper for g in group for n in neighbours(g)):
        return None
    moves.append(Move(dropper, target, merge=True))
    return Plan(item_id, k, moves) if len(moves) <= max_moves else None
