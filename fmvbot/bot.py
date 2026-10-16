"""The main loop: observe the game, let the highest-priority task act, repeat."""

import asyncio
import logging
import time
from collections import Counter, deque
from pathlib import Path

from playwright.async_api import Error as PlaywrightError

from . import ui
from .config import Config
from .game import Blueprints, Cell, Snapshot
from .inputs import Pointer
from .session import GameNotFound, Session

log = logging.getLogger("fmvbot")


class Bot:
    def __init__(self, session: Session, cfg: Config, log_dir: Path | None = None):
        from .tasks import build_tasks

        self.s = session
        self.cfg = cfg
        self.ptr = Pointer(session, speed=cfg.speed)
        self.bp = Blueprints({})
        self.recipes: dict = {}
        self.snap: Snapshot | None = None
        self.stats: Counter = Counter()
        self.log_dir = log_dir
        self._blocked: dict[str, float] = {}
        self._fails: Counter = Counter()
        self._actions: deque[float] = deque()
        self._started = time.monotonic()
        self._last_report = 0.0
        self._next_break = self._started + cfg.break_every_minutes * 60 if cfg.break_every_minutes else None
        self.tasks = build_tasks(self, cfg.tasks)

    # ------------------------------------------------------------ lifecycle

    async def connect(self):
        await self.s.find_game()
        delay = 2.0
        while True:
            try:
                info = await self.s.ensure_agent()
                if info.get("scene"):
                    break
                log.info("game is loading...")
            except (PlaywrightError, GameNotFound) as e:
                log.info("game not ready yet (%s)", str(e).splitlines()[0][:120])
                if isinstance(e, GameNotFound) or (self.s.frame and self.s.frame.is_detached()):
                    await self.s.find_game()
            await asyncio.sleep(delay)
            delay = min(delay * 1.5, 15)
        self.bp = Blueprints(await self.s.blueprints())
        self.recipes = await self.s.recipes()
        await self.s.refresh_origin()
        log.info("connected: %d blueprints, %d game services", len(self.bp.db), len(info.get("globalServices", [])))

    async def run(self, max_minutes: float = 0):
        await self.connect()
        end = time.monotonic() + max_minutes * 60 if max_minutes else None
        while end is None or time.monotonic() < end:
            try:
                snap = await self.observe()
                if not snap.in_game:
                    await asyncio.sleep(2)
                    continue
                await self._maybe_break()
                self._report(snap)
                if not await self.tick(snap):
                    await self.idle(snap)
            except (GameNotFound, PlaywrightError) as e:
                log.warning("lost the game (%s); reconnecting", str(e).splitlines()[0][:160])
                await self._screenshot("lost")
                await self._reconnect()

    async def _reconnect(self):
        delay = 3.0
        while True:
            await asyncio.sleep(delay)
            try:
                await self.connect()
                return
            except (GameNotFound, PlaywrightError) as e:
                log.warning("reconnect failed (%s); retrying", str(e).splitlines()[0][:160])
                delay = min(delay * 2, 60)

    def _frame_gone(self) -> bool:
        return self.s.frame is None or self.s.frame.is_detached()

    async def tick(self, snap: Snapshot) -> bool:
        # on someone else's farm only popups and the visit itself make sense
        tasks = [t for t in self.tasks if t.name in ("popups", "visits")] if snap.visiting else self.tasks
        if snap.overlays:
            # the sticker book covers everything; nothing else can be pressed until it's gone
            tasks = [t for t in self.tasks if t.name == "stickers"]
        for task in tasks:
            if self.blocked(f"task:{task.name}"):
                continue
            try:
                # earlier tasks may have waited; give each one the latest view of the game
                if await task.step(self.snap or snap):
                    self.stats[task.name] += 1
                    return True
            except GameNotFound:
                raise
            except Exception as e:
                if isinstance(e, PlaywrightError) and self._frame_gone():
                    raise
                log.exception("task %s failed", task.name)
                self.block(f"task:{task.name}", 60)
                await self._screenshot(f"error-{task.name}")
                return True
        return False

    async def idle(self, snap: Snapshot):
        wait = self.cfg.idle_poll
        timers = [it.timer_left() for it in snap.items if it.busy]
        if timers:
            wait = min(wait, max(2.0, min(timers) + 1))
        log.debug("idle %.0fs", wait)
        await asyncio.sleep(wait)

    def _report(self, snap: Snapshot, every: float = 300):
        now = time.monotonic()
        if now - self._last_report < every:
            return
        self._last_report = now
        exp, nxt = snap.raw.get("exp") or 0, snap.raw.get("nextExp") or 0
        progress = f" ({100 * exp // nxt}%)" if nxt else ""
        done = ", ".join(f"{k} {v}" for k, v in sorted(self.stats.items()) if k not in ("taps", "drags"))
        log.info(
            "level %d%s | coins %d gems %d energy %d crates %d | %d free cells | %s",
            snap.level,
            progress,
            snap.amount("coins"),
            snap.amount("gems"),
            snap.amount("energy"),
            snap.amount("crates"),
            len(snap.empty),
            done or "nothing yet",
        )

    async def _maybe_break(self):
        if self._next_break and time.monotonic() > self._next_break:
            log.info("taking a %.0f minute break", self.cfg.break_minutes)
            await asyncio.sleep(self.cfg.break_minutes * 60)
            self._next_break = time.monotonic() + self.cfg.break_every_minutes * 60

    # ------------------------------------------------------------ observing

    async def observe(self) -> Snapshot:
        snap = await self.s.snapshot()
        if not snap.ok:
            # the agent survived a game reload but lost its handles
            await self.s.ensure_agent()
            snap = await self.s.snapshot()
        if self.snap is not None and snap.level != self.snap.level and snap.level:
            log.info("level up! now level %d", snap.level)
            self.recipes = await self.s.recipes()
        self.snap = snap
        return snap

    async def settle(self, timeout: float = 2.5):
        """Wait for the camera to stop moving."""
        prev = await self.s.camera()
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            await asyncio.sleep(0.12)
            cur = await self.s.camera()
            if cur == prev:
                return
            prev = cur

    async def wait_for(self, pred, timeout: float = 3.0, interval: float = 0.25) -> Snapshot | None:
        end = time.monotonic() + timeout
        while True:
            snap = await self.observe()
            try:
                if pred(snap):
                    return snap
            except (KeyError, IndexError, AttributeError, TypeError):
                pass
            if time.monotonic() > end:
                return None
            await asyncio.sleep(interval)

    # ------------------------------------------------------------ bookkeeping

    def reachable(self, cell: Cell) -> bool:
        """False for a while after a cell turned out to be hidden behind a taller sprite."""
        return not self.blocked(f"covered:{cell}")

    def block(self, key: str, seconds: float):
        self._blocked[key] = time.monotonic() + seconds

    def blocked(self, key: str) -> bool:
        until = self._blocked.get(key)
        if until is None:
            return False
        if time.monotonic() > until:
            del self._blocked[key]
            return False
        return True

    def failed(self, key: str, limit: int = 2, seconds: float = 300) -> int:
        self._fails[key] += 1
        if self._fails[key] >= limit:
            log.info("giving up on %s for %.0f minutes", key, seconds / 60)
            self.block(key, seconds)
            self._fails[key] = 0
        return self._fails[key]

    def succeeded(self, key: str):
        self._fails.pop(key, None)

    async def _rate_limit(self):
        now = time.monotonic()
        while self._actions and now - self._actions[0] > 60:
            self._actions.popleft()
        if len(self._actions) >= self.cfg.max_actions_per_minute:
            await asyncio.sleep(60 - (now - self._actions[0]))
        self._actions.append(time.monotonic())

    async def _screenshot(self, tag: str):
        if not (self.cfg.screenshots_on_error and self.log_dir and self.s.page):
            return
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            path = self.log_dir / f"{time.strftime('%Y%m%d-%H%M%S')}-{tag}.png"
            await self.s.page.screenshot(path=str(path))
            log.info("screenshot saved: %s", path)
        except PlaywrightError:
            pass

    # ------------------------------------------------------------ acting

    async def _point(self, cell: Cell, refocus: bool = True, pick: bool = True) -> tuple[float, float] | None:
        """Where to press for a cell. When picking an object up or tapping it, find a spot that
        isn't covered by a taller sprite in front; drops just need the tile itself."""
        for attempt in range(2 if refocus else 1):
            if attempt:
                await self.s.focus_cell(*cell)
                await self.settle()
            if pick:
                p = await self.s.pick_point(*cell)
                if p and not self.snap.clickable_point(p["x"], p["y"]):
                    # off screen: the game hides what the camera can't see, so it can't be picked yet
                    continue
                if p and p.get("ok") is False and p.get("popout") and await self.dismiss_popouts():
                    p = await self.s.pick_point(*cell)
                if p and p.get("ok") is False:
                    log.debug("cell %s is covered by %s", cell, p.get("blocker"))
                    self.block(f"covered:{cell}", 120)
                    return None
            else:
                p = await self.s.cell_css(*cell)
            if p and self.snap.clickable_point(p["x"], p["y"]):
                return p["x"], p["y"]
        return None

    async def tap_cell(self, cell: Cell) -> bool:
        await self.settle(1.0)
        pt = await self._point(cell)
        if pt is None:
            log.debug("cell %s is not reachable on screen", cell)
            return False
        if not await self._safe(*pt):
            return False
        await self._rate_limit()
        await self.ptr.tap(*pt, jitter=self._jitter())
        self.stats["taps"] += 1
        return True

    def _inside(self, x: float, y: float, quiet: bool = False) -> bool:
        """Never press anything outside the game itself (the page around it may be a website or Discord)."""
        v = self.snap.view if self.snap else {}
        m = 8  # leave room for the pointer's jitter
        ok = (
            v.get("l", 0) + m <= x <= v.get("l", 0) + v.get("w", 0) - m
            and v.get("t", 0) + m <= y <= v.get("t", 0) + v.get("h", 0) - m
        )
        if not ok and not quiet:
            log.warning("refusing to press outside the game view at (%.0f, %.0f)", x, y)
        return ok

    async def _safe(self, x: float, y: float) -> bool:
        if not self._inside(x, y):
            return False
        await self.s.refresh_origin()
        if not await self.s.game_on_top(*self.s.to_page(x, y)):
            log.warning("something is covering the game at (%.0f, %.0f); not pressing there", x, y)
            return False
        return True

    async def tap_xy(self, x: float, y: float, jitter: float = 2.0):
        if not await self._safe(x, y):
            return
        await self._rate_limit()
        await self.ptr.tap(x, y, jitter=jitter)
        self.stats["taps"] += 1

    async def drag_xy(self, x1: float, y1: float, x2: float, y2: float) -> bool:
        if not (await self._safe(x1, y1) and await self._safe(x2, y2)):
            return False
        await self._rate_limit()
        await self.ptr.drag(x1, y1, x2, y2, jitter=2)
        return True

    async def drag_cells(self, src: Cell, dst: Cell) -> bool:
        await self.settle(1.0)
        # picking up needs the item itself; the drop only goes by the tile under the pointer
        a = await self._point(src, refocus=False)
        b = await self._point(dst, refocus=False, pick=False)
        if a is None or b is None:
            if not self.reachable(src):
                return False
            mid = ((src[0] + dst[0]) // 2, (src[1] + dst[1]) // 2)
            await self.s.focus_cell(*mid)
            await self.settle()
            a = await self._point(src, refocus=False)
            b = await self._point(dst, refocus=False, pick=False)
        if a is None or b is None:
            log.debug("cannot see both %s and %s", src, dst)
            return False
        if not (await self._safe(*a) and await self._safe(*b)):
            return False
        await self._rate_limit()
        await self.ptr.drag(*a, *b, jitter=self._jitter())
        self.stats["drags"] += 1
        return True

    def _jitter(self) -> float:
        cam = self.snap.camera if self.snap else None
        # roughly 4% of a tile, whatever the zoom
        return max(1.0, min(4.0, (cam or {}).get("a", 0.5) * 6))

    async def dismiss_popouts(self) -> bool:
        """Close the little menus over board objects the way a player would: tap bare ground."""
        snap = await self.observe()
        if not snap.popouts:
            return False
        boxes = [
            (p["x"] - p["w"] / 2 - 10, p["y"] - p["h"] / 2 - 10, p["x"] + p["w"] / 2 + 10, p["y"] + p["h"] / 2 + 10)
            for p in snap.popouts
        ]
        for x, y in sorted(snap.empty.values(), key=lambda pt: -min(abs(pt[0] - b[0]) + abs(pt[1] - b[1]) for b in boxes)):
            if snap.clickable_point(x, y) and not any(x0 <= x <= x1 and y0 <= y <= y1 for x0, y0, x1, y1 in boxes):
                await self.tap_xy(x, y)
                await self.wait_for(lambda s: not s.popouts, 1.5)
                return True
        return False

    # ------------------------------------------------------------ items

    def repair_needs(self) -> dict[str, int]:
        """Materials that broken buildings already on the farm are waiting for."""
        snap = self.snap
        need: dict[str, int] = {}
        if snap is None:
            return need
        on_farm = {(self.bp.comp(it.id, "building") or {}).get("ID") for it in snap.items if it.has("building")}
        for bid, info in snap.buildings.items():
            if info.get("active") or bid not in on_farm:
                continue
            for c in info.get("upgradeCost") or []:
                need[c["blueprintID"]] = need.get(c["blueprintID"], 0) + c["amount"]
        return need

    @staticmethod
    def popout_button(snap: Snapshot, name: str, near: tuple[float, float]) -> dict | None:
        buttons = [b for p in snap.popouts for b in p["ui"] if b["name"] == name]
        return min(buttons, key=lambda b: (b["x"] - near[0]) ** 2 + (b["y"] - near[1]) ** 2, default=None)

    def _menu_button(self, snap: Snapshot, item, match) -> dict | None:
        cur = snap.grid.get(item.cell)
        near = (cur.x, cur.y) if cur else (item.x, item.y)
        cands = [b for p in snap.popouts for b in p["ui"] if match(b)]
        return min(cands, key=lambda b: (b["x"] - near[0]) ** 2 + (b["y"] - near[1]) ** 2, default=None)

    async def item_menu(self, item, match) -> dict | None:
        """Tap a board object and press the button in the little menu that opens over it.
        Returns the button pressed, or None. Buttons that cost gems or money are never pressed."""
        match_free = match

        def match(b):
            return match_free(b) and not ui.spends_premium(b)

        for attempt in range(2):
            if attempt:
                # the menu opened past the edge of the screen: bring the object to the middle
                await self.dismiss_popouts()
                await self.s.focus_cell(*item.cell)
                await self.settle()
            if not await self.tap_cell(item.cell):
                return None
            snap = await self.wait_for(lambda s: self._menu_button(s, item, match) is not None, 2)
            if snap is None:
                return None
            b = self._menu_button(snap, item, match)
            if self._inside(b["x"], b["y"], quiet=True):
                await self.tap_xy(b["x"], b["y"])
                return b
        return None

    async def collect_item(self, item) -> bool:
        """Tap an item that goes to the inventory; currencies ask for a confirm in a popout."""
        if not await self.tap_cell(item.cell):
            return False

        def gone(s):
            cur = s.grid.get(item.cell)
            return cur is None or cur.id != item.id

        def where(s):
            cur = s.grid.get(item.cell)
            return (cur.x, cur.y) if cur else (item.x, item.y)

        snap = await self.wait_for(lambda s: gone(s) or self.popout_button(s, "collectButton", where(s)), timeout=2)
        if snap is None:
            return False
        if gone(snap):
            return True
        b = self.popout_button(snap, "collectButton", where(snap))
        if not self._inside(b["x"], b["y"], quiet=True):
            # the menu opened past the edge of the screen: bring the item to the middle and retry
            await self.dismiss_popouts()
            await self.s.focus_cell(*item.cell)
            await self.settle()
            if not await self.tap_cell(item.cell):
                return False
            snap = await self.wait_for(lambda s: gone(s) or self.popout_button(s, "collectButton", where(s)), timeout=2)
            if snap is None:
                return False
            if gone(snap):
                return True
            b = self.popout_button(snap, "collectButton", where(snap))
        await self.tap_xy(b["x"], b["y"])
        return await self.wait_for(gone, timeout=2.5) is not None

    # ------------------------------------------------------------ popups

    async def open_popup(self, cell: Cell, name: str | None = None, timeout: float = 3.0) -> dict | None:
        """Tap something on the board and wait for its popup to be ready for input."""
        if not await self.tap_cell(cell):
            return None
        snap = await self.wait_for(
            lambda s: (
                s.popups
                and (name is None or name in s.top_popup["name"])
                and not s.top_popup["interactionsLocked"]
                and any(b["w"] > 4 for b in s.top_popup["ui"])
            ),
            timeout=timeout,
        )
        if snap is None:
            return None
        return await self.stable_popup()

    async def stable_popup(self, timeout: float = 2.5) -> dict | None:
        """Popups scale in; wait until the buttons stop moving."""

        def layout(p):
            return [(b["name"], b["x"], b["y"], b["w"], b["h"]) for b in p["ui"]] if p else None

        prev = None
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            snap = await self.observe()
            p = snap.top_popup
            if p and not p["interactionsLocked"] and layout(p) == prev:
                return p
            prev = layout(p)
            await asyncio.sleep(0.15)
        return self.snap.top_popup

    def button(self, popup: dict | None, name: str | None = None, tex: str | None = None) -> dict | None:
        if not popup:
            return None
        for b in popup["ui"]:
            if name is not None and b["name"] != name:
                continue
            if tex is not None and not any(tex in t for t in b.get("tex") or []):
                continue
            if b["w"] > 4 and b["h"] > 4:
                return b
        return None

    async def press(self, popup_name: str, name: str | None = None, tex: str | None = None) -> bool:
        p = await self.stable_popup()
        if not p or popup_name not in p["name"] or p["interactionsLocked"]:
            return False
        b = self.button(p, name=name, tex=tex)
        if b is None:
            return False
        await self.tap_xy(b["x"], b["y"])
        return True

    async def close_popups(self):
        for _ in range(4):
            snap = await self.observe()
            if not snap.popups:
                return
            p = snap.top_popup
            if p["interactionsLocked"]:
                await asyncio.sleep(0.4)
                continue
            b = self.button(p, name="close")
            if b is not None:
                await self.tap_xy(b["x"], b["y"])
            else:
                await self.s.eval("() => window.__fmvAgent.closeTopPopup()")
            await self.wait_for(lambda s: len(s.popups) < len(snap.popups), timeout=2)
