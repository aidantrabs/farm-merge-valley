import math
import re

from .. import ui
from ..game import Snapshot
from . import Task, log

AVAILABLE, AFFORDABLE = 1, 3


class LandTask(Task):
    """Expand the farm: bank enough coins for the cheapest plot, then press its Buy button."""

    name = "land"

    def coin_value(self, item_id: str) -> int:
        return sum(r.get("amount", 0) for r in self.bp.collect_reward(item_id) if r.get("key") == "coins")

    def plots(self, snap: Snapshot) -> list[dict]:
        allowed = {"level", "coins"}
        return [
            a
            for a in snap.areas
            if set(a["req"]) <= allowed and a["req"].get("level", 0) <= snap.level and not self.bot.blocked(f"land:{a['id']}")
        ]

    async def step(self, snap) -> bool:
        if not self.cfg.unlock_land or snap.popups or snap.tutorial.get("track"):
            return False
        plots = self.plots(snap)
        if not plots:
            return False
        for a in plots:
            if a["state"] == AFFORDABLE and snap.amount("coins") - a["req"].get("coins", 0) >= self.cfg.land_coin_reserve:
                return await self.buy(a, snap)
        cheapest = min(plots, key=lambda a: a["req"].get("coins", 0))
        need = cheapest["req"].get("coins", 0) + self.cfg.land_coin_reserve - snap.amount("coins")
        if need <= 0:
            return False
        coins = sorted(
            (it for it in snap.items if it.has("coin") and it.has("collectable") and not self.bot.blocked(f"collect:{it.cell}")),
            key=lambda it: self.coin_value(it.id),
        )
        if sum(self.coin_value(it.id) for it in coins) < need:
            return False
        # the smallest coin that covers the gap, otherwise the biggest one
        it = next((c for c in coins if self.coin_value(c.id) >= need), coins[-1])
        log.info("banking %s for land %s (%d more coins needed)", it.id, cheapest["id"], need)
        if not await self.bot.collect_item(it):
            self.bot.failed(f"collect:{it.cell}", limit=2, seconds=300)
        return True

    async def buy(self, area: dict, snap: Snapshot) -> bool:
        key = f"land:{area['id']}"

        def button(s: Snapshot):
            # the Buy bubble floats over its own plot; anything further away belongs to another one
            here = next((a for a in s.areas if a["id"] == area["id"]), area)
            reach = max(4 * (s.tile_h or 50), 120)
            cands = [
                b
                for b in s.map_ui
                if b["name"] == "purchaseArea"
                and not ui.spends_premium(b)
                and math.hypot(b["x"] - here["x"], b["y"] - here["y"]) <= reach
            ]
            return min(cands, key=lambda b: math.hypot(b["x"] - here["x"], b["y"] - here["y"]), default=None)

        b = button(snap)
        if b is None or not snap.clickable_point(b["x"], b["y"]):
            cells = area["cells"]
            mc, mr = sum(c for c, _ in cells) / len(cells), sum(r for _, r in cells) / len(cells)
            c, r = min(cells, key=lambda cr: math.hypot(cr[0] - mc, cr[1] - mr))
            await self.bot.s.focus_cell(c, r)
            await self.bot.settle()
            snap = await self.bot.observe()
            b = button(snap)
        if b is None or not snap.clickable_point(b["x"], b["y"]):
            self.bot.failed(key, limit=2, seconds=600)
            return False
        log.info("buying land %s for %d coins", area["id"], area["req"].get("coins", 0))
        await self.bot.tap_xy(b["x"], b["y"])
        done = await self.bot.wait_for(lambda s: not any(a["id"] == area["id"] for a in s.areas), timeout=4)
        if done is None:
            self.bot.failed(key, limit=2, seconds=600)
        else:
            self.bot.stats["land"] += 1
        return True


class RepairTask(Task):
    """Fix broken buildings once their materials are on the board."""

    name = "repair"

    async def step(self, snap) -> bool:
        if snap.popups or snap.tutorial.get("track"):
            return False
        for it in snap.items:
            bid = (self.bp.comp(it.id, "building") or {}).get("ID")
            info = snap.buildings.get(bid or "", {})
            if not bid or info.get("active") or self.bot.blocked(f"repair:{bid}"):
                continue
            if it.has("undiscoveredBuilding"):
                # the first tap introduces the building and shows what it needs
                log.info("discover %s", bid)
                await self.bot.tap_cell(it.cell)
                await self.bot.wait_for(lambda s: s.popups or s.popouts, 2)
                self.bot.block(f"repair:{bid}", 30)
                return True
            cost = info.get("upgradeCost") or []
            if not cost or not all(len(snap.by_id(c["blueprintID"])) >= c["amount"] for c in cost):
                continue
            return await self.repair(bid, it, cost)
        return False

    async def repair(self, bid: str, it, cost) -> bool:
        key = f"repair:{bid}"
        log.info("repair %s with %s", bid, ", ".join(f"{c['amount']}x {c['blueprintID']}" for c in cost))
        if await self.bot.item_menu(it, lambda b: b["name"].startswith("requirements") or b["text"].lower() == "repair") is None:
            self.bot.failed(key, limit=2, seconds=600)
            return True
        done = await self.bot.wait_for(lambda s: s.buildings.get(bid, {}).get("active"), 6)
        # give the repair animation (and any celebration popup) time before looking again
        self.bot.block(key, 120)
        if done is None:
            self.bot.failed(key, limit=2, seconds=600)
        else:
            self.bot.stats["repairs"] += 1
        return True


class CardsTask(Task):
    """Upgrade cards drop while harvesting; use them on their crop or animal to raise its yield."""

    name = "cards"
    USE = re.compile(r"\b(use|apply|upgrade|redeem|activate)\b", re.I)

    async def step(self, snap) -> bool:
        if snap.popups or snap.tutorial.get("track") or self.bot.blocked("cards"):
            return False
        cards = [it for it in snap.items if it.has("upgradeCard") and self.bot.reachable(it.cell)]
        if not cards:
            return False
        self.bot.block("cards", 900)
        card = max(cards, key=lambda it: self.bp.tier(it.id))
        await self.bot.tap_cell(card.cell)
        snap = await self.bot.wait_for(lambda s: s.popups or s.popouts, 2)
        if snap is None:
            return True
        buttons = [b for p in snap.popups[-1:] for b in p["ui"]] + [b for p in snap.popouts for b in p["ui"]]
        use = next((b for b in buttons if self.USE.search(b["text"] or b["name"]) and not ui.costs_something(b)), None)
        if use is None:
            log.info("upgrade card: no 'use' button (%s)", "; ".join(ui.label(b)[:30] for b in buttons) or "nothing")
            await self.bot.close_popups()
            await self.bot.dismiss_popouts()
            return True
        log.info("using %s", card.id)
        await self.bot.tap_xy(use["x"], use["y"])
        self.bot.stats["cards used"] += 1
        await self.bot.wait_for(lambda s: not s.grid.get(card.cell) or s.grid[card.cell].id != card.id, 3)
        return True
