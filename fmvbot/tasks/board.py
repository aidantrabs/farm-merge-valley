import asyncio
import re

from .. import ui
from ..game import Item, Snapshot
from . import Task, log
from .orders import missing_ingredients


def _hp(it: Item) -> int:
    hp = it.data.get("hitpoints") or {}
    return hp.get("current", 1) if isinstance(hp, dict) else 1


def _cooling(it: Item) -> bool:
    # a cooldown we can't read the timer of counts as still running
    return it.busy or (it.has("cooldown") and "cooldown" not in it.timers)


class CollectTask(Task):
    """Pick up things that belong in the inventory: produce, tickets, supplies, and currency when it's time."""

    name = "collect"

    def reason(self, it: Item, snap: Snapshot) -> str | None:
        if it.has("ingredient"):
            return "produce"
        if it.has("ticketAnimation") or it.id == "ticket":
            return "ticket"
        if not it.has("collectable"):
            return None
        if it.has("energy"):
            top = self.bp.next(it.id) is None
            if not self.cfg.keep_energy_items or top:
                return "energy"
            if snap.amount("energy") < self.cfg.energy_low and self._energy_wanted(snap):
                return "energy (running low)"
            return None
        if it.has("coin"):
            if self.bp.next(it.id) is None or self.bp.tier(it.id) >= self.cfg.collect_coins_at_tier:
                return "coins"
            return None
        if it.has("gem"):
            if not self.cfg.collect_gems_at_tier:
                return None
            if self.bp.next(it.id) is None or self.bp.tier(it.id) >= self.cfg.collect_gems_at_tier:
                return "gems"
            return None
        if not it.has("mergeable", "currency"):
            # supply crates and other one-off pickups
            return "pickup"
        return None

    def _energy_wanted(self, snap: Snapshot) -> bool:
        return (snap.workers.get("free") or 0) > 0 and any(it.has("mapSource") for it in snap.items)

    async def step(self, snap) -> bool:
        if snap.tutorial.get("blockTaps"):
            return False
        picks = [
            (it, why)
            for it in snap.items
            if (why := self.reason(it, snap)) and not self.bot.blocked(f"collect:{it.cell}") and self.bot.reachable(it.cell)
        ]
        if not picks:
            return False
        picks.sort(key=lambda p: (p[1] != "produce", p[0].r, p[0].c))
        it, why = picks[0]
        log.info("collect %s at %s (%s)", it.id, it.cell, why)
        if await self.bot.collect_item(it):
            self.bot.stats["collected"] += 1
        else:
            self.bot.failed(f"collect:{it.cell}", limit=2, seconds=300)
        return True


class HarvestTask(Task):
    """Tap fully grown crops and animals that are ready."""

    name = "harvest"

    async def step(self, snap) -> bool:
        if snap.tutorial.get("blockTaps") or len(snap.empty) < 3:
            return False
        ready = [
            it
            for it in snap.items
            if it.has("harvestable")
            and not _cooling(it)
            and not (it.harvest or {}).get("depleted")
            and _hp(it) > 0
            and not self.bot.blocked(f"harvest:{it.cell}")
        ]
        wanted = missing_ingredients(self.bot, snap)
        if not ready:
            return await self.speed_up(snap, wanted)
        it = min(ready, key=lambda i: self.bp.produce(i.id) not in wanted)
        log.info("harvest %s at %s", it.id, it.cell)
        before = len(snap.items)
        if not await self.bot.tap_cell(it.cell):
            self.bot.failed(f"harvest:{it.cell}", seconds=300)
            return True
        done = await self.bot.wait_for(
            lambda s: len(s.items) > before or (s.grid.get(it.cell) and s.grid[it.cell].busy), timeout=2.5
        )
        if done is None:
            self.bot.failed(f"harvest:{it.cell}", seconds=600)
        else:
            self.bot.stats["harvests"] += 1
        return True

    async def speed_up(self, snap, wanted: dict[str, int]) -> bool:
        """Skip a crop or animal's regrowth with gems, but only when an open order is waiting for what it makes."""
        if not self.cfg.spend_gems:
            return False
        cooling = [
            it
            for it in snap.items
            if it.has("harvestable")
            and it.busy
            and not (it.harvest or {}).get("depleted")
            and self.bp.produce(it.id) in wanted
            and not self.bot.blocked(f"skip:{it.cell}")
        ]
        if not cooling:
            return False
        it = min(cooling, key=lambda i: i.timer_left())
        if not await self.skip_cooldown(it, f"{it.id} regrowth"):
            self.bot.block(f"skip:{it.cell}", 300)
        return True


class ClearDepletedTask(Task):
    """Remove crops and animals that have no harvests left (they leave a couple of seedlings behind)."""

    name = "clear_depleted"

    async def step(self, snap) -> bool:
        if snap.tutorial.get("blockTaps") or len(snap.empty) < 2:
            return False
        for it in snap.items:
            h = it.harvest or {}
            if not h.get("depleted") or it.busy or self.bot.blocked(f"clear:{it.cell}"):
                continue
            cost = (h.get("removalCost") or {}).get("cost") or {}
            if not isinstance(cost, dict) or (cost.get("amount") and cost.get("key") != "energy"):
                continue
            if cost.get("amount") and snap.amount("energy") < cost["amount"]:
                continue
            log.info("clear depleted %s at %s", it.id, it.cell)
            await self.bot.tap_cell(it.cell)
            gone = await self.bot.wait_for(lambda s: s.grid.get(it.cell) is None or s.grid[it.cell].id != it.id, timeout=3)
            if gone is None:
                self.bot.failed(f"clear:{it.cell}", limit=1, seconds=900)
            return True
        return False


# crates refill over time only up to this many; above it regeneration is wasted
CRATE_CAP = 40


class CratesTask(Task):
    """Open supply crates while there is room on the board."""

    name = "crates"

    async def step(self, snap) -> bool:
        btn = snap.hud.get("spawnCrate")
        crates = snap.amount("crates")
        # at the cap every crate not opened is one lost, so dig deeper into the reserve
        reserve = self.cfg.reserve_free_cells if crates < CRATE_CAP else self.cfg.min_free_cells + 1
        room = len(snap.empty) - reserve
        if snap.popups or snap.tutorial.get("blockTaps"):
            return False
        if crates <= 0 and room > 0 and self.cfg.spend_gems and not self.bot.blocked("crates:buy"):
            return await self.buy(snap)
        if not btn or crates <= 0 or room <= 0 or self.bot.blocked("crates"):
            return False
        n = min(self.cfg.crate_burst, room, crates)
        log.info("open %d crate%s (%d left, %d free cells)", n, "s" * (n > 1), crates, len(snap.empty))
        for _ in range(n):
            cur = await self.bot.observe()
            if cur.popups or cur.amount("crates") <= 0 or "spawnCrate" not in cur.hud:
                break
            await self.bot.tap_xy(btn["x"], btn["y"], jitter=6)
            await self.bot.ptr.pause(0.25, 0.5)
        after = await self.bot.wait_for(lambda s: s.amount("crates") < crates, timeout=2.5)
        if after is None:
            self.bot.failed("crates", limit=2, seconds=120)
        else:
            self.bot.stats["crates"] += crates - after.amount("crates")
        await asyncio.sleep(0.4)
        return True

    async def buy(self, snap) -> bool:
        """Out of crates: buy the pack with the most crates per gem that the gem settings allow."""
        bot = self.bot
        bot.block("crates:buy", 600)
        market = snap.hud.get("marketplace")
        if market is None:
            return False
        await bot.tap_xy(market["x"], market["y"])
        if await bot.wait_for(lambda s: s.popups and "MarketPlace" in s.top_popup["name"], 3) is None:
            return True
        for _ in range(8):
            p = await bot.stable_popup()
            if not p or "MarketPlace" not in p["name"]:
                break
            packs = [(b, int(m.group(1)), ui.gem_price(b)) for b in p["ui"] if (m := CRATE_PACK.match(b["name"] or ""))]
            packs = [(b, n, price) for b, n, price in packs if self.gems_ok(price, bot.snap)]
            if not packs:
                log.info("no crate pack within the gem settings")
                break
            b, n, price = max(packs, key=lambda t: (t[1] / t[2], t[1]))
            v = bot.snap.view
            if bot.snap.on_screen(b["x"], b["y"], margin=24):
                before = bot.snap.amount("crates")
                if await self.pay_gems(b, f"buy {n} crates") or bot.snap.amount("crates") > before:
                    bot.stats["crates bought"] += n
                break
            # the packs sit further down the shop: scroll the list towards them
            x, mid = v["l"] + v["w"] / 2, v["t"] + v["h"] / 2
            step = v["h"] * 0.3 * (1 if b["y"] > mid else -1)
            await bot.drag_xy(x, mid + step / 2, x, mid - step / 2)
            await asyncio.sleep(0.6)
        await bot.close_popups()
        return True


CRATE_PACK = re.compile(r"^purchaseInventory-crates_(\d+)$")


class TidyTask(Task):
    """When the board is nearly full and nothing merges, bank currency to make room. A completely
    full board is a dead end: the game won't even harvest without a free cell for the produce."""

    name = "tidy"

    def candidates(self, snap):
        cash = [
            it
            for it in snap.items
            if it.has("collectable")
            and (it.has("coin", "energy") or (it.has("gem") and self.cfg.collect_gems_at_tier))
            and not self.bot.blocked(f"collect:{it.cell}")
            and self.bot.reachable(it.cell)
        ]
        # cheapest first; energy items are a reserve, so they go last
        return sorted(cash, key=lambda i: (i.has("energy"), self.bp.tier(i.id), i.has("gem")))

    async def step(self, snap) -> bool:
        if len(snap.empty) >= self.cfg.min_free_cells or snap.tutorial.get("blockTaps") or self.bot.blocked("tidy"):
            return False
        cash = self.candidates(snap)
        if not cash:
            self.bot.block("tidy", 300)
            log.warning("board is full and nothing can be merged or banked")
            await self.bot._screenshot("board-full")
            return False
        it = cash[0]
        log.info("making room: banking %s at %s", it.id, it.cell)
        if not await self.bot.collect_item(it):
            self.bot.failed(f"collect:{it.cell}", limit=1, seconds=300)
        return True
