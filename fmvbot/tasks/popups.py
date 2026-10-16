import asyncio
import re
import time

from .. import ui
from . import Task, log

SHOP = re.compile(r"(market|shop|offer|premium|purchase|iap|sale|deal|store|pass|payment|advert|piggy|special)", re.I)


class PopupTask(Task):
    """Deal with whatever popup is on top: claim rewards, dismiss everything else."""

    name = "popups"

    def __init__(self, bot):
        super().__init__(bot)
        self.seen: set[str] = set()
        self.attempts: dict[str, list[float]] = {}
        self.locked_since: dict[str, float] = {}

    def _attempt(self, name: str) -> int:
        now = time.monotonic()
        recent = [t for t in self.attempts.get(name, []) if now - t < 90] + [now]
        self.attempts[name] = recent
        return len(recent)

    async def step(self, snap) -> bool:
        p = snap.top_popup
        if not p or self.bot.blocked("popups"):
            return False
        if snap.tutorial.get("track") and snap.tutorial.get("hand"):
            return False
        name = p["name"]
        key = f"{name}#{p.get('id')}"
        if p["interactionsLocked"]:
            # popups ignore input while animating; one that never stops is a stuck game
            since = self.locked_since.setdefault(key, time.monotonic())
            if time.monotonic() - since < 45:
                await asyncio.sleep(0.5)
                return True
            log.warning("popup %s has ignored input for 45s", name)
            self.locked_since.pop(key, None)
            await self.bot._screenshot(f"locked-popup-{name}")
            self.bot.block("popups", 180)
            return False
        self.locked_since.pop(key, None)
        view = snap.view
        buttons = [b for b in p["ui"] if b["w"] < view["w"] * 0.9 or b["h"] < view["h"] * 0.9]
        if name not in self.seen:
            self.seen.add(name)
            log.info("popup %s: %s", name, "; ".join(ui.label(b)[:50] for b in buttons) or "no buttons")

        n = self._attempt(key)
        if n > 6:
            log.warning("popup %s will not go away; pausing popup handling", name)
            await self.bot._screenshot(f"stuck-popup-{name}")
            self.bot.block("popups", 300)
            return False
        if n > 3:
            if p["locked"]:
                return False
            log.info("closing popup %s directly", name)
            await self.bot.s.eval("() => window.__fmvAgent.closeTopPopup()")
            await asyncio.sleep(0.8)
            return True

        target, why = self._choose(p, buttons, snap, outside_ok=n == 1)
        if target is None:
            if p["locked"]:
                # a popup that can't be dismissed is waiting for something else (often the tutorial)
                return False
            await self.bot.s.eval("() => window.__fmvAgent.closeTopPopup()")
            await asyncio.sleep(0.8)
            return True
        log.debug("popup %s -> %s (%s)", name, why, ui.label(target) if isinstance(target, dict) else target)
        x, y = (target["x"], target["y"]) if isinstance(target, dict) else target
        await self.bot.tap_xy(x, y)
        await self.bot.wait_for(
            lambda s: not s.popups or s.top_popup["name"] != name or len(s.popups) != len(snap.popups),
            timeout=2.5,
        )
        return True

    @staticmethod
    def _outside(buttons, snap):
        """A spot off the popup that isn't a HUD button either: popups that don't dim the screen
        let taps through, and the edges of the screen are where the HUD buttons live."""
        v = snap.view
        cx, cy = v["l"] + v["w"] / 2, v["t"] + v["h"] / 2
        spots = [(v["l"] + 12, cy), (v["l"] + v["w"] - 12, cy), (cx, v["t"] + v["h"] - 12), (cx, v["t"] + 12)]
        boxes = [(b["x"], b["y"], b["w"] / 2 + 6, b["h"] / 2 + 6) for b in buttons] + [
            ((x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2 + 6, (y1 - y0) / 2 + 6) for x0, y0, x1, y1 in snap.hud_rects()
        ]
        for x, y in spots:
            if not any(abs(x - bx) <= hw and abs(y - by) <= hh for bx, by, hw, hh in boxes):
                return x, y
        return None

    def _choose(self, p, buttons, snap, outside_ok: bool):
        view = snap.view
        spot = self._outside(buttons, snap) if outside_ok and not p["locked"] else None
        outside = (spot, "tap outside") if spot else (None, "nothing left to try")
        if p["tapToContinue"]:
            # tap somewhere that isn't a button (offers like "x2 for gems" sit right under the text)
            spots = [(0.5, 0.6), (0.5, 0.9), (0.12, 0.5), (0.88, 0.5), (0.5, 0.12)]
            for fx, fy in spots:
                x, y = view["l"] + view["w"] * fx, view["t"] + view["h"] * fy
                if not any(abs(x - b["x"]) <= b["w"] / 2 + 6 and abs(y - b["y"]) <= b["h"] / 2 + 6 for b in buttons):
                    return (x, y), "tap to continue"
            return None, "nowhere safe to tap"
        if SHOP.search(p["name"]):
            b = ui.close_button(buttons) or next((b for b in buttons if ui.is_negative(b)), None)
            return (b, "dismiss shop") if b else outside
        good = [b for b in buttons if ui.is_positive(b)]
        if good:
            return ui.biggest(good), "positive"
        b = ui.close_button(buttons)
        if b and not p["locked"]:
            return b, "close"
        safe = [b for b in buttons if not ui.costs_something(b)]
        if len(safe) == 1:
            return safe[0], "only button"
        return outside
