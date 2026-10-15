import asyncio
import re
import time

from .. import ui
from ..game import Snapshot
from . import Task, log


def _tex(b: dict) -> str:
    return " ".join(b.get("tex") or [])


def free_timed(b: dict, ads: bool) -> bool:
    """Marketplace freebies: 'purchaseTime_*' with a FREE label. '_rv' ones play a video ad first."""
    name = b.get("name") or ""
    text = (b.get("text") or "").lower()
    if not name.startswith("purchaseTime") or "free" not in text or re.search(r"\d", text):
        return False
    return ads or ("_rv" not in name and "video" not in _tex(b))


class RewardsTask(Task):
    """Collect the free stuff: discovery book gems, timed marketplace gifts, daily challenge rewards."""

    name = "rewards"

    async def step(self, snap: Snapshot) -> bool:
        if snap.popups or snap.tutorial.get("track"):
            return False
        hud = snap.hud
        if snap.raw.get("unclaimedDiscoveries") and "discovery" in hud and not self.bot.blocked("rewards:discovery"):
            self.bot.block("rewards:discovery", 600)
            return await self.discoveries(hud["discovery"])
        market = hud.get("marketplace")
        if market and not self.bot.blocked("rewards:market"):
            self.bot.block("rewards:market", 1800 if not market.get("attention") else 600)
            return await self.marketplace(market)
        daily = hud.get("dailyChallenge")
        if daily and daily.get("attention") and not self.bot.blocked("rewards:daily"):
            self.bot.block("rewards:daily", 900)
            return await self.daily_challenge(daily)
        return False

    async def _open(self, button: dict, name: str) -> dict | None:
        await self.bot.tap_xy(button["x"], button["y"])
        snap = await self.bot.wait_for(lambda s: s.popups and name in s.top_popup["name"], 3)
        return await self.bot.stable_popup() if snap else None

    async def discoveries(self, button: dict) -> bool:
        popup = await self._open(button, "Discovery")
        if popup is None:
            return True
        start = self.bot.snap.raw.get("unclaimedDiscoveries") or 0
        scrolls = 0
        stale = 0
        tapped: set[tuple[int, int]] = set()
        for _ in range(40):
            snap = await self.bot.observe()
            p = snap.top_popup
            left = snap.raw.get("unclaimedDiscoveries") or 0
            if not p or "Discovery" not in p["name"] or not left:
                break
            # gifts wobble while opening, so tap each one once per pass rather than hammering the first
            gifts = [
                b
                for b in p["ui"]
                if "gift_box" in _tex(b)
                and snap.on_screen(b["x"], b["y"])
                and (round(b["x"] / 20), round(b["y"] / 20)) not in tapped
            ]
            if gifts:
                # one gift opens at a time; taps during the opening animation are ignored
                g = gifts[0]
                tapped.add((round(g["x"] / 20), round(g["y"] / 20)))
                await self.bot.tap_xy(g["x"], g["y"], jitter=2)
                await self.bot.wait_for(lambda s: (s.raw.get("unclaimedDiscoveries") or 0) < left, 1.6, 0.15)
                continue
            if tapped:
                # everything visible has been tried once; look again from scratch
                after = (await self.bot.observe()).raw.get("unclaimedDiscoveries") or 0
                stale = stale + 1 if after == left else 0
                tapped.clear()
                if stale < 2:
                    continue
            tabs = [
                b
                for b in p["ui"]
                if "icon_tab_discoverybook" in _tex(b)
                and "icon_notification_attention" in _tex(b)
                and "tab_active" not in _tex(b)
            ]
            if tabs:
                await self.bot.tap_xy(tabs[0]["x"], tabs[0]["y"])
                scrolls = 0
                await asyncio.sleep(0.6)
                continue
            if scrolls >= 6:
                break
            # more gifts further down the list
            panel = p["ui"][1] if len(p["ui"]) > 1 else None
            if panel is None:
                break
            x, y = panel["x"], panel["y"] + panel["h"] * 0.25
            await self.bot.drag_xy(x, y, x, y - panel["h"] * 0.45)
            scrolls += 1
            await asyncio.sleep(0.6)
        claimed = start - (self.bot.snap.raw.get("unclaimedDiscoveries") or 0)
        if claimed:
            log.info("claimed %d discovery rewards", claimed)
            self.bot.stats["discoveries"] += claimed
        await self.bot.close_popups()
        return True

    async def marketplace(self, button: dict) -> bool:
        popup = await self._open(button, "MarketPlace")
        if popup is None:
            return True
        tabs = [b for b in popup["ui"] if b["text"] and "popup_tab" in _tex(b) and b["w"] < 200]
        visited = 0
        for tab in [None, *tabs]:
            if tab is not None:
                await self.bot.tap_xy(tab["x"], tab["y"])
                await asyncio.sleep(0.6)
            p = await self.bot.stable_popup()
            if not p or "MarketPlace" not in p["name"]:
                break
            for b in p["ui"]:
                if free_timed(b, self.cfg.watch_ads):
                    log.info("free %s from the marketplace", b["name"].removeprefix("purchaseTime_"))
                    await self.bot.tap_xy(b["x"], b["y"])
                    await self.bot.wait_for(lambda s: not s.popups or "MarketPlace" not in s.top_popup["name"], 2)
                    self.bot.stats["freebies"] += 1
                    # a reward popup may sit on top now; let the popup task deal with it after we leave
                    break
            visited += 1
        await self.bot.close_popups()
        return True

    async def daily_challenge(self, button: dict) -> bool:
        popup = await self._open(button, "")
        if popup is None:
            return True
        for _ in range(6):
            p = await self.bot.stable_popup()
            if not p:
                break
            good = [b for b in p["ui"] if ui.is_positive(b) and not ui.costs_something(b) and not ui.is_close(b)]
            if not good:
                break
            await self.bot.tap_xy(good[0]["x"], good[0]["y"])
            self.bot.stats["daily rewards"] += 1
            await asyncio.sleep(0.8)
        await self.bot.close_popups()
        return True


class VisitsTask(Task):
    """Spend train tickets visiting farms (yours gets the same boosts you give), then come home.

    Only builds with social features have a train station; elsewhere this does nothing.
    """

    name = "visits"
    VISIT = re.compile(r"\b(visit|travel|go|let'?s go)\b", re.I)
    HOME = re.compile(r"^(home|back|backhome|gohome|returnhome|homebutton|leave|leavefarm)$", re.I)

    def __init__(self, bot):
        super().__init__(bot)
        self.started: float | None = None
        self.helped: set = set()
        self.logged = False

    async def step(self, snap: Snapshot) -> bool:
        if snap.visiting:
            return await self.on_visit(snap)
        self.started = None
        if not self.cfg.visit_friends or snap.amount("tickets") < 1 or snap.popups or snap.tutorial.get("track"):
            return False
        if self.bot.blocked("visits"):
            return False
        station = next((it for it in snap.items if it.has("trainstation")), None)
        if station is None:
            self.bot.block("visits", 3600)
            return False
        self.bot.block("visits", 900)
        log.info("using a train ticket (%d left)", snap.amount("tickets"))
        await self.bot.tap_cell(station.cell)
        opened = await self.bot.wait_for(lambda s: s.popups or s.visiting, 3)
        if opened is None:
            return True
        if opened.visiting:
            return True
        p = await self.bot.stable_popup()
        if p and not self.logged:
            self.logged = True
            log.info("train station popup %s: %s", p["name"], "; ".join(ui.label(b)[:40] for b in p["ui"]))
        buttons = [b for b in (p or {}).get("ui", []) if not ui.costs_something(b) and not ui.is_close(b)]
        go = next((b for b in buttons if self.VISIT.search(b["text"] or b["name"] or "")), None)
        if go is None:
            await self.bot.close_popups()
            return True
        await self.bot.tap_xy(go["x"], go["y"])
        if await self.bot.wait_for(lambda s: s.visiting, 15) is not None:
            self.started = time.monotonic()
            self.helped = set()
            self.bot.stats["visits"] += 1
        return True

    def targets(self, snap: Snapshot):
        """What visitors can help with: the train, the shop, one tree or rock, a grown animal and crop."""
        kinds = {
            "train": [it for it in snap.items if it.has("trainstation")],
            "shop": [it for it in snap.items if it.has("workshop") and it.has("building")],
            "nature": [it for it in snap.items if it.has("mapSource")],
            "animal": [it for it in snap.items if it.has("harvestable") and it.has("animal")],
            "crop": [it for it in snap.items if it.has("harvestable") and it.has("crop")],
        }
        for kind, items in kinds.items():
            if kind not in self.helped and items:
                yield kind, items[0]

    async def on_visit(self, snap: Snapshot) -> bool:
        if self.started is None:
            self.started = time.monotonic()
        if snap.popups:
            return False
        if time.monotonic() - self.started < 90:
            for kind, it in self.targets(snap):
                self.helped.add(kind)
                log.info("visit: helping with the %s", kind)
                await self.bot.tap_cell(it.cell)
                menu = await self.bot.wait_for(
                    lambda s: any(not ui.costs_something(b) and b["name"] for p in s.popouts for b in p["ui"]), 1.5
                )
                if menu is not None:
                    b = next(b for p in menu.popouts for b in p["ui"] if not ui.costs_something(b) and b["name"])
                    await self.bot.tap_xy(b["x"], b["y"])
                await asyncio.sleep(0.8)
                return True
        home = next((b for n, b in snap.hud.items() if self.HOME.search(n)), None)
        log.info("visit done, going home")
        if home is not None:
            await self.bot.tap_xy(home["x"], home["y"])
        else:
            await self.bot.s.eval("() => window.__fmvAgent.goHome()")
        if await self.bot.wait_for(lambda s: not s.visiting, 15) is None:
            await self.bot.s.eval("() => window.__fmvAgent.goHome()")
            await self.bot.wait_for(lambda s: not s.visiting, 15)
        self.started = None
        return True
