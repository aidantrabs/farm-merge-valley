import asyncio
import math
import time

from .. import ui
from . import Task, log
from .progress import LandTask

# buttons in the small menus that open over board objects
POPOUT_ACTIONS = ("collectButton", "remove", "purchaseArea")


def _progress(t: dict):
    return (t.get("track"), str(t.get("drags")), str(t.get("taps")))


def _nearest(hand: dict, buttons: list[dict]) -> dict | None:
    if not buttons:
        return None
    b = min(buttons, key=lambda b: math.hypot(b["x"] - hand["x"], b["y"] - hand["y"]))
    reach = max(hand["w"], hand["h"]) * 1.6 + max(b["w"], b["h"]) / 2
    return b if math.hypot(b["x"] - hand["x"], b["y"] - hand["y"]) <= reach else None


class TutorialTask(Task):
    """Follow the game's onboarding: it whitelists the drag or tap it wants, or points at it with a hand."""

    name = "tutorial"

    def __init__(self, bot):
        super().__init__(bot)
        self.no_hand: dict[str, float] = {}

    async def step(self, snap) -> bool:
        t = snap.tutorial
        track = t.get("track")
        if not track:
            return False
        key = f"tutorial:{track}"
        if self.bot.blocked(key):
            return False
        hand = t.get("hand")

        if snap.popups:
            # inside a popup the hand is the only hint about which button it wants
            popup = snap.top_popup
            if popup["interactionsLocked"]:
                return False
            # the hand only appears once the step is listening for the press; pressing before that
            # (while the popup is still opening) skips the step and the tutorial never continues.
            # It also blinks, so wait for it to show rather than for it to stay.
            if not hand:
                if time.monotonic() < self.no_hand.get(popup["name"], 0):
                    return False
                hand = await self._wait_for_hand(4.0)
                if not hand:
                    # e.g. a level-up popup in the middle of the tutorial: the popup task handles it
                    self.no_hand[popup["name"]] = time.monotonic() + 20
                    return False
            snap = self.bot.snap
            popup = snap.top_popup
            if not popup or popup["interactionsLocked"]:
                return False
            view = snap.view
            buttons = [
                b for b in popup["ui"] if (b["w"] < view["w"] * 0.9 or b["h"] < view["h"] * 0.9) and not ui.costs_something(b)
            ]
            b = _nearest(hand, buttons)
            if b is None:
                return False
            log.info("tutorial %s: press %s in %s", track, b["name"] or b["text"] or "button", popup["name"])
            await self.bot.tap_xy(b["x"], b["y"])
            return await self._after(True, key, snap)

        drags = [d for d in t.get("drags") or [] if d.get("from") and d.get("to")]
        taps = [c for c in t.get("taps") or [] if c]
        # prefer whitelisted cells that still hold something (e.g. the coin not yet picked up)
        taps = [c for c in taps if snap.grid.get(tuple(c))] or taps
        if drags:
            d = drags[0]
            log.info("tutorial %s: drag %s -> %s", track, d["from"], d["to"])
            ok = await self.bot.drag_cells(tuple(d["from"]), tuple(d["to"]))
            return await self._after(ok, key, snap)

        # the land tutorial waits for the Buy bubble, not for the cell it whitelists
        affordable = [a for a in snap.areas if a["state"] == 3 and set(a["req"]) <= {"level", "coins"}]
        if affordable:
            log.info("tutorial %s: buy land", track)
            ok = await LandTask(self.bot).buy(affordable[0], snap)
            return await self._after(ok, key, snap)

        if taps:
            cell = tuple(taps[0])
            log.info("tutorial %s: tap %s", track, cell)
            ok = await self.bot.tap_cell(cell)
            if ok:
                # tapping coins and the like opens a small menu whose button is the real step
                it = snap.grid.get(cell)
                near = (it.x, it.y) if it else (0, 0)
                menu = await self.bot.wait_for(
                    lambda s: any(b["name"] in POPOUT_ACTIONS for p in s.popouts for b in p["ui"]), 1.5
                )
                if menu is not None:
                    b = min(
                        (b for p in menu.popouts for b in p["ui"] if b["name"] in POPOUT_ACTIONS),
                        key=lambda b: math.hypot(b["x"] - near[0], b["y"] - near[1]),
                    )
                    log.info("tutorial %s: press %s", track, b["name"])
                    await self.bot.tap_xy(b["x"], b["y"])
            return await self._after(ok, key, snap)

        if hand:
            b = _nearest(hand, [dict(v, name=k) for k, v in snap.hud.items()])
            if b is not None:
                log.info("tutorial %s: tap %s", track, b["name"])
                await self.bot.tap_xy(b["x"], b["y"])
                return await self._after(True, key, snap)

        if t.get("dialog") and t.get("blockTaps") and t.get("blockDrags"):
            view = snap.view
            await self.bot.tap_xy(view["l"] + view["w"] / 2, view["t"] + view["h"] * 0.75)
            return await self._after(True, key, snap)
        return False

    async def _wait_for_hand(self, timeout: float) -> dict | None:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            snap = await self.bot.observe()
            if not snap.popups:
                return None
            if snap.tutorial.get("hand"):
                return snap.tutorial["hand"]
            await asyncio.sleep(0.15)
        return None

    async def _after(self, ok: bool, key: str, before) -> bool:
        if not ok:
            self.bot.failed(key, limit=3, seconds=60)
            return False

        # some steps need several actions (e.g. open three crates) before the tutorial moves on,
        # so any change to the farm counts as the action having worked
        def moved(s):
            return (
                _progress(s.tutorial) != _progress(before.tutorial)
                or len(s.popups) != len(before.popups)
                or len(s.items) != len(before.items)
                or s.inv != before.inv
            )

        changed = await self.bot.wait_for(moved, timeout=3)
        if changed is None:
            self.bot.failed(key, limit=4, seconds=60)
        else:
            self.bot.succeeded(key)
        await asyncio.sleep(0.3)
        return True
