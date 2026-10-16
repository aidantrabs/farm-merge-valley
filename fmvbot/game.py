"""Typed views over the agent's snapshot and the blueprint database."""

from dataclasses import dataclass, field

Cell = tuple[int, int]

NEIGHBOURS = ((1, 0), (-1, 0), (0, 1), (0, -1))


def neighbours(cell: Cell):
    c, r = cell
    for dc, dr in NEIGHBOURS:
        yield (c + dc, r + dr)


@dataclass
class Item:
    id: str
    c: int
    r: int
    x: float
    y: float
    behaviors: frozenset[str]
    data: dict = field(default_factory=dict)
    ext: tuple[int, int] = (1, 1)
    timers: dict = field(default_factory=dict)
    harvest: dict | None = None

    @property
    def cell(self) -> Cell:
        return (self.c, self.r)

    def has(self, *names: str) -> bool:
        return any(n in self.behaviors for n in names)

    @property
    def movable(self) -> bool:
        return "movable" in self.behaviors

    @property
    def busy(self) -> bool:
        """Something is counting down on it (cooldown, worker, production...)."""
        return any(t and t.get("state") == "ACTIVE" and (t.get("remaining") or 0) > 0 for t in self.timers.values())

    def timer_left(self) -> float:
        left = [t.get("remaining") or 0 for t in self.timers.values() if t and t.get("state") == "ACTIVE"]
        return max(left) / 1000 if left else 0.0

    def footprint(self) -> list[Cell]:
        w, h = self.ext
        return [(self.c - i, self.r - j) for i in range(w) for j in range(h)]


class Snapshot:
    def __init__(self, raw: dict | None):
        raw = raw or {"ok": False}
        self.raw = raw
        self.ok: bool = bool(raw.get("ok"))
        self.inv: dict = raw.get("inv") or {}
        self.level: int = raw.get("level") or 0
        self.popups: list[dict] = raw.get("popups") or []
        self.popup_open: bool = bool(raw.get("popupOpen"))
        self.overlays: list[dict] = raw.get("overlays") or []
        self.hud: dict = raw.get("hud") or {}
        self.scene: str | None = raw.get("scene")
        self.camera: dict | None = raw.get("camera")
        self.tile_h: float = raw.get("tileH") or 0.0
        self.tutorial: dict = raw.get("tutorial") or {}
        self.workers: dict = raw.get("workers") or {}
        self.view: dict = raw.get("view") or {}
        self.visiting: bool = bool(raw.get("visiting"))
        self.connected = raw.get("connected")
        self.orders: list[dict] = raw.get("orders") or []
        self.buildings: dict[str, dict] = raw.get("buildings") or {}
        self.popouts: list[dict] = raw.get("popouts") or []
        self.areas: list[dict] = raw.get("areas") or []
        self.map_ui: list[dict] = raw.get("mapUi") or []
        self.items: list[Item] = [
            Item(
                id=e["id"],
                c=e["c"],
                r=e["r"],
                x=e["x"],
                y=e["y"],
                behaviors=frozenset(e.get("b") or ()),
                data=e.get("d") or {},
                ext=tuple(e.get("ext") or (1, 1)),
                timers={k: v for k, v in (e.get("timers") or {}).items() if v},
                harvest=e.get("harvest"),
            )
            for e in raw.get("cells") or []
        ]
        self.empty: dict[Cell, tuple[float, float]] = {(e[0], e[1]): (e[2], e[3]) for e in raw.get("empty") or []}
        self.grid: dict[Cell, Item] = {}
        for it in self.items:
            for cell in it.footprint():
                self.grid[cell] = it

    @property
    def in_game(self) -> bool:
        return self.ok and self.scene is not None

    @property
    def top_popup(self) -> dict | None:
        return self.popups[-1] if self.popups else None

    def amount(self, key: str) -> int:
        return int(self.inv.get(key) or 0)

    def by_id(self, item_id: str) -> list[Item]:
        return [it for it in self.items if it.id == item_id]

    def with_behavior(self, *names: str) -> list[Item]:
        return [it for it in self.items if it.has(*names)]

    def is_free(self, cell: Cell) -> bool:
        return cell in self.empty

    def on_screen(self, x: float, y: float, margin: float = 0.0) -> bool:
        w, h = self.view.get("w", 0), self.view.get("h", 0)
        left, top = self.view.get("l", 0), self.view.get("t", 0)
        return left + margin <= x <= left + w - margin and top + margin <= y <= top + h - margin

    def hud_rects(self) -> list[tuple[float, float, float, float]]:
        out = []
        for e in self.hud.values():
            out.append((e["x"] - e["w"] / 2, e["y"] - e["h"] / 2, e["x"] + e["w"] / 2, e["y"] + e["h"] / 2))
        return out

    def clickable_point(self, x: float, y: float, pad: float = 6.0) -> bool:
        """Inside the game view, away from the edges and not under a HUD button."""
        w, h = self.view.get("w", 0), self.view.get("h", 0)
        if not self.on_screen(x, y, margin=max(30.0, min(w, h) * 0.07)):
            return False
        return not any(x0 - pad <= x <= x1 + pad and y0 - pad <= y <= y1 + pad for x0, y0, x1, y1 in self.hud_rects())


class Blueprints:
    def __init__(self, db: dict):
        self.db = db
        # which ingredient each merge chain eventually produces (chicken -> egg)
        self._produce: dict[str, str] = {}
        for bp in db.values():
            if bp.get("harvestable") and bp.get("collectItem") and bp.get("graph"):
                self._produce[bp["graph"]] = bp["collectItem"]

    def get(self, item_id: str) -> dict:
        return self.db.get(item_id) or {}

    def comp(self, item_id: str, name: str):
        return (self.get(item_id).get("components") or {}).get(name)

    def has(self, item_id: str, name: str) -> bool:
        return name in (self.get(item_id).get("components") or {})

    def next(self, item_id: str) -> str | None:
        m = self.comp(item_id, "mergeable")
        return m.get("target") if isinstance(m, dict) else None

    def mergeable(self, item_id: str) -> bool:
        return self.next(item_id) is not None

    def graph(self, item_id: str) -> str:
        g = self.get(item_id).get("graph")
        if g:
            return g
        head, _, tail = item_id.rpartition("_")
        return head if tail.isdigit() else item_id

    def tier(self, item_id: str) -> int:
        t = self.get(item_id).get("tier")
        if isinstance(t, int):
            return t
        _, _, tail = item_id.rpartition("_")
        return int(tail) if tail.isdigit() else 0

    def produce(self, item_id: str) -> str | None:
        return self._produce.get(self.graph(item_id))

    def collect_reward(self, item_id: str) -> list[dict]:
        c = self.comp(item_id, "collectable")
        return (c or {}).get("reward") or []
