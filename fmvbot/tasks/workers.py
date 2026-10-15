import math

from .. import ui
from ..game import Item, Snapshot
from . import Task, log

FREE_SKIP_S = 30


def stage_cost(it: Item) -> float:
    """Energy for the next step; anything gated on gems or coins is off limits."""
    gate = it.data.get("resourceGate") or {}
    cost = gate.get("cost") or []
    if any(not isinstance(c, dict) or c.get("key") != "energy" for c in cost):
        return math.inf
    return sum(c.get("amount", 0) for c in cost)


class WorkersTask(Task):
    """Trees, rocks and toolboxes: send workers (costs energy), collect what they drop."""

    name = "workers"

    def sources(self, snap: Snapshot) -> list[Item]:
        return [it for it in snap.items if it.has("mapSource") and not self.bot.blocked(f"source:{it.cell}")]

    def kind(self, it: Item) -> str:
        if "toolbox" in it.id:
            return "toolbox"
        if "rock" in it.id:
            return "rock"
        if "tree" in it.id:
            return "tree"
        return "other"

    def wanted(self, it: Item, snap: Snapshot) -> tuple:
        """Lower sorts first: toolboxes, then what pending repairs need, then the cheapest step."""
        kinds = self.cfg.use_energy_on
        rank = kinds.index(self.kind(it)) if self.kind(it) in kinds else len(kinds)
        need = self.bot.repair_needs()
        material = {"tree": "wood", "rock": "stone", "toolbox": "tool"}.get(self.kind(it), "")
        feeds_repair = bool(material) and any(n.startswith(material) for n in need)
        hp = it.data.get("hitpoints")
        hp = hp.get("current", 9) if isinstance(hp, dict) else 9
        return (rank, not feeds_repair, stage_cost(it), hp)

    async def step(self, snap) -> bool:
        if snap.tutorial.get("blockTaps") or snap.popups:
            return False
        sources = self.sources(snap)
        if not sources:
            return False

        # finished jobs leave loot behind; it drops onto nearby empty cells
        for it in sources:
            loot = (it.data.get("lootable") or {}).get("loot") or []
            if it.has("lootable") and not it.busy and len(snap.empty) >= min(len(loot), 4):
                log.info("collect %s from %s", ", ".join(loot) or "loot", it.id)
                before = len(snap.items)
                await self.bot.tap_cell(it.cell)
                if await self.bot.wait_for(lambda s: len(s.items) != before or not s.grid.get(it.cell), 2.5) is None:
                    self.bot.failed(f"source:{it.cell}", limit=2, seconds=300)
                else:
                    self.bot.stats["loot"] += 1
                return True

        # the game offers a free skip on the map when a timer is nearly done
        for b in snap.map_ui:
            if b["name"] == "cooldownFree" and snap.clickable_point(b["x"], b["y"]) and not self.bot.blocked("skip:map"):
                log.info("free skip")
                await self.bot.tap_xy(b["x"], b["y"])
                if await self.bot.wait_for(lambda s: not any(x["name"] == "cooldownFree" for x in s.map_ui), 2) is None:
                    self.bot.block("skip:map", 60)
                return True

        # a job about to finish can be skipped for free
        for it in sources:
            if it.busy and 0 < it.timer_left() <= FREE_SKIP_S and not self.bot.blocked(f"skip:{it.cell}"):
                return await self.free_skip(it)

        free_workers = snap.workers.get("free") or 0
        if free_workers <= 0:
            return False
        idle = [
            it
            for it in sources
            if it.has("resourceGate")
            and not it.busy
            and not it.has("lootable", "workerData")
            and self.kind(it) in self.cfg.use_energy_on
        ]
        energy = snap.amount("energy") - self.cfg.energy_reserve
        idle = [it for it in idle if stage_cost(it) <= energy]
        if not idle:
            return False
        it = min(idle, key=lambda i: self.wanted(i, snap))
        return await self.start(it)

    async def start(self, it: Item) -> bool:
        key = f"source:{it.cell}"
        log.info("send worker to %s at %s (%d energy)", it.id, it.cell, stage_cost(it))
        if await self.bot.item_menu(it, lambda b: b["name"] == "remove") is None:
            self.bot.failed(key, limit=2, seconds=600)
            return True
        ok = await self.bot.wait_for(lambda s: s.grid.get(it.cell) is not None and s.grid[it.cell].busy, 3)
        if ok is None:
            self.bot.failed(key, limit=2, seconds=600)
        else:
            self.bot.stats["workers sent"] += 1
        return True

    async def free_skip(self, it: Item) -> bool:
        await self.bot.tap_cell(it.cell)
        snap = await self.bot.wait_for(lambda s: any(ui.is_free(b) for p in s.popouts for b in p["ui"]), 1.5)
        if snap is None:
            self.bot.block(f"skip:{it.cell}", 60)
            return True
        b = next(b for p in snap.popouts for b in p["ui"] if ui.is_free(b))
        log.info("free skip on %s", it.id)
        await self.bot.tap_xy(b["x"], b["y"])
        await self.bot.wait_for(lambda s: s.grid.get(it.cell) is None or not s.grid[it.cell].busy, 2)
        return True
