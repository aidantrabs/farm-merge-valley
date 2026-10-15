import math

from ..game import Item, Snapshot
from ..planner import plan_group
from . import Task, log
from .merging import MergeTask

TIERS = ("bronze", "silver", "gold")


def _reward(it: Item, bp) -> dict:
    return it.data.get("crateReward") or bp.comp(it.id, "crateReward") or {}


class ChestsTask(Task):
    """Open reward chests with their keys (they need room for every reward inside),
    and turn 5 keys into 2 of the next tier when a better chest is waiting for them."""

    name = "chests"

    def requirements(self, it: Item) -> list[dict]:
        return _reward(it, self.bp).get("crateRewardUnlockRequirement") or []

    def space_needed(self, it: Item) -> int:
        return len(_reward(it, self.bp).get("rewards") or []) or 6

    async def step(self, snap: Snapshot) -> bool:
        if snap.popups or snap.tutorial.get("track"):
            return False
        chests = [it for it in snap.items if it.has("crateReward") and not self.bot.blocked(f"chest:{it.cell}")]
        # chests disappear after a while: deal with the oldest first
        chests.sort(key=lambda it: it.timer_left() or math.inf)
        for ch in chests:
            req = self.requirements(ch)
            if not all(len(snap.by_id(r["blueprintID"])) >= r["amount"] for r in req):
                continue
            need = self.space_needed(ch)
            if len(snap.empty) < need:
                return await self.make_room(snap, need)
            return await self.open(ch)
        return await self.upgrade_keys(snap, chests)

    async def make_room(self, snap: Snapshot, need: int) -> bool:
        cash = [
            it
            for it in snap.items
            if it.has("coin", "gem")
            and it.has("collectable")
            and not self.bot.blocked(f"collect:{it.cell}")
            and self.bot.reachable(it.cell)
        ]
        # only worth banking if it can actually free enough cells
        if len(snap.empty) + len(cash) < need:
            return False
        it = min(cash, key=lambda i: self.bp.tier(i.id))
        log.info("making room for a chest (%d free, %d needed): banking %s", len(snap.empty), need, it.id)
        if not await self.bot.collect_item(it):
            self.bot.failed(f"collect:{it.cell}", limit=1, seconds=300)
        return True

    async def open(self, ch: Item) -> bool:
        key = f"chest:{ch.cell}"
        log.info("open %s", ch.id.replace("reward_crate_", "") + " chest")
        if await self.bot.item_menu(ch, lambda b: b["name"].startswith("requirements") or b["text"].lower() == "open") is None:
            self.bot.failed(key, limit=2, seconds=600)
            return True
        gone = await self.bot.wait_for(lambda s: s.grid.get(ch.cell) is None or s.grid[ch.cell].id != ch.id, 5)
        if gone is None:
            self.bot.failed(key, limit=2, seconds=600)
        else:
            self.bot.stats["chests"] += 1
        return True

    async def upgrade_keys(self, snap: Snapshot, chests: list[Item]) -> bool:
        """5 keys make 2 of the next tier (3 only make 1, which is never worth it)."""
        waiting = [(r["blueprintID"], r["amount"]) for ch in chests for r in self.requirements(ch)]
        for key_id, amount in dict.fromkeys(waiting):
            tier = next((t for t in TIERS if t in key_id), None)
            if tier is None or tier == "bronze":
                continue
            lower = key_id.replace(tier, TIERS[TIERS.index(tier) - 1])
            if len(snap.by_id(key_id)) >= amount or self.bot.blocked(f"merge:{lower}"):
                continue
            # keep enough lower keys for chests of the lower tier that are also waiting
            keep = sum(a for k, a in waiting if k == lower)
            copies = sorted(it.cell for it in snap.by_id(lower) if it.movable)
            if len(copies) - keep < 5:
                continue
            frozen = set(copies[:keep]) if keep else set()
            cells = set(copies) - frozen
            pushable = {it.cell for it in snap.items if it.movable and it.has("displaceable") and it.ext == (1, 1)} - set(copies)
            plan = plan_group(lower, cells, 5, set(snap.empty), self.cfg.max_moves_per_merge, pushable, frozen=frozen)
            if plan is None:
                continue
            log.info("upgrading 5 %s keys into 2 %s keys", TIERS[TIERS.index(tier) - 1], tier)
            await MergeTask(self.bot).execute(plan)
            return True
        return False
