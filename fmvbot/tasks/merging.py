import asyncio
from collections import defaultdict

from ..game import Snapshot
from ..planner import Plan, plan_group
from . import Task, log
from .orders import missing_ingredients

# chests and keys are spent by the chests task, not merged blindly
NEVER = ("reward_crate",)


class MergeTask(Task):
    name = "merge"

    def _matches(self, item_id: str, names: list[str]) -> bool:
        graph = self.bp.graph(item_id)
        return any(item_id == n or graph == n or item_id.startswith(n) for n in names)

    def allow3(self, item_id: str, tight: bool) -> bool:
        if self._matches(item_id, self.cfg.merge5_only):
            return False
        return tight or not self.cfg.prefer_merge5 or self._matches(item_id, self.cfg.merge3_always)

    def plans(self, snap: Snapshot) -> list[Plan]:
        free = {c for c in snap.empty if self.bot.reachable(c)}
        tight = len(free) < self.cfg.merge3_when_free_below
        groups: dict[str, set] = defaultdict(set)
        hidden: dict[str, set] = defaultdict(set)
        pushable = set()
        for it in snap.items:
            if it.movable and it.has("displaceable") and not it.busy and it.ext == (1, 1):
                pushable.add(it.cell)
            if not self.bp.mergeable(it.id):
                continue
            if not it.movable or it.busy:
                # still an identical neighbour as far as the game is concerned
                hidden[it.id].add(it.cell)
                continue
            if not self.bot.reachable(it.cell):
                hidden[it.id].add(it.cell)
                continue
            if self._matches(it.id, self.cfg.never_merge) or it.id.startswith(NEVER):
                continue
            groups[it.id].add(it.cell)
        # leave alone the copies a broken building is waiting for, and ones we can't get at
        frozen: dict[str, set] = defaultdict(set, {k: set(v) for k, v in hidden.items()})
        for item_id, n in self.bot.repair_needs().items():
            if item_id in groups:
                keep = set(sorted(groups[item_id])[:n])
                frozen[item_id] |= keep
                groups[item_id] -= keep
        out = []
        for item_id, cells in groups.items():
            if self.bot.blocked(f"merge:{item_id}"):
                continue
            others = pushable - cells
            plan = None
            if len(cells) >= 5:
                plan = plan_group(
                    item_id, cells, 5, free, self.cfg.max_moves_per_merge, others, allow_oversize=True, frozen=frozen[item_id]
                )
            if plan is None and len(cells) >= 3 and self.allow3(item_id, tight):
                plan = plan_group(
                    item_id, cells, 3, free, self.cfg.max_moves_per_merge, others, allow_oversize=tight, frozen=frozen[item_id]
                )
            if plan:
                out.append(plan)
        # cheap merges first, then producers of ingredients that orders are waiting for, then the highest tier
        wanted = missing_ingredients(self.bot, snap)
        out.sort(key=lambda p: (p.cost, self.bp.produce(p.item_id) not in wanted, -self.bp.tier(p.item_id), p.distance))
        return out

    async def step(self, snap) -> bool:
        if snap.tutorial.get("blockDrags"):
            return False
        plans = self.plans(snap)
        if not plans:
            return False
        await self.execute(plans[0])
        return True

    async def execute(self, plan: Plan):
        bot = self.bot
        key = f"merge:{plan.item_id}"
        log.info("merge %dx %s (%d move%s)", plan.size, plan.item_id, len(plan.moves), "s" * (len(plan.moves) > 1))
        for mv in plan.moves:
            snap = bot.snap
            src = snap.grid.get(mv.src)
            if src is None or src.id != plan.item_id:
                return
            if not mv.merge and not mv.displace and mv.dst not in snap.empty:
                return
            count = len(snap.by_id(plan.item_id))
            if not await bot.drag_cells(mv.src, mv.dst):
                bot.failed(key, limit=2, seconds=180)
                return
            if mv.merge:
                # every merge eats at least three; leftovers of odd-sized groups stay behind
                ok = await bot.wait_for(lambda s: len(s.by_id(plan.item_id)) <= count - 3, timeout=3)
            else:
                ok = await bot.wait_for(lambda s: s.grid.get(mv.dst) is not None and s.grid[mv.dst].id == plan.item_id, timeout=2)
            if ok is None:
                log.info("merge step %s -> %s did not land", mv.src, mv.dst)
                bot.failed(key, limit=2, seconds=300)
                return
            await asyncio.sleep(0.15)
        bot.succeeded(key)
        bot.stats["merges"] += 1
