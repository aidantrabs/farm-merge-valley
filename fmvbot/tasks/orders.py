from .. import ui
from ..game import Snapshot
from . import Task, log

# order states as the game stores them; the game picks each building's next recipe itself
WAITING, READY, MAKING, DONE = 0, 1, 2, 3
FREE_SKIP_MS = 30_000


def missing_ingredients(bot, snap: Snapshot) -> dict[str, int]:
    """Ingredients that open orders are still short of, so harvests and merges can aim for them."""
    short: dict[str, int] = {}
    for o in snap.orders:
        if o["state"] not in (WAITING, READY):
            continue
        for i in (bot.recipes.get(o["recipe"]) or {}).get("ingredients") or []:
            gap = i["amount"] - snap.amount(i["key"])
            if gap > 0:
                short[i["key"]] = short.get(i["key"], 0) + gap
    return short


class OrdersTask(Task):
    """Production buildings: start orders when the ingredients are in, skip timers (free, or with gems
    when allowed), claim the coins."""

    name = "orders"

    def affordable(self, recipe_id: str, snap: Snapshot) -> bool:
        r = self.bot.recipes.get(recipe_id) or {}
        return bool(r) and all(snap.amount(i["key"]) >= i["amount"] for i in r.get("ingredients") or [])

    async def step(self, snap) -> bool:
        if snap.tutorial.get("track") or snap.popups:
            return False
        sites = {}
        for it in snap.items:
            b = self.bp.comp(it.id, "building")
            if it.has("workshop") and isinstance(b, dict) and b.get("ID"):
                sites[b["ID"]] = it
        for o in snap.orders:
            bid = o["building"]
            it = sites.get(bid)
            info = snap.buildings.get(bid, {})
            if it is None or not info.get("active", True) or info.get("upgrading") or self.bot.blocked(f"orders:{bid}"):
                continue
            if o["state"] == DONE:
                if len(snap.empty) < len((self.bot.recipes.get(o["recipe"]) or {}).get("reward") or [1]):
                    continue
                return await self.claim(bid, it, o)
            if o["state"] == MAKING:
                left = (o.get("timer") or {}).get("remaining") or 0
                if 0 < left <= FREE_SKIP_MS or (left > 0 and self.cfg.spend_gems):
                    return await self.skip(bid, it)
                continue
            if o["state"] in (WAITING, READY) and self.affordable(o["recipe"], snap):
                return await self.make(bid, it, o)
        return False

    async def claim(self, bid, it, o) -> bool:
        log.info("claim %s from %s", o["recipe"].removeprefix("recipe_"), bid)
        await self.bot.tap_cell(it.cell)

        def claimed(s):
            return not any(x["building"] == bid and x["state"] == DONE for x in s.orders)

        done = await self.bot.wait_for(claimed, 3)
        if done is None:
            # some versions open the order popup instead of claiming straight away
            if await self.bot.press("Orders", name="orderClaim"):
                done = await self.bot.wait_for(claimed, 3)
            await self.bot.close_popups()
        if done is None:
            self.bot.failed(f"orders:{bid}", limit=2, seconds=300)
        else:
            self.bot.stats["orders claimed"] += 1
        return True

    def skip_button(self, popup) -> dict | None:
        """The fast-forward button if it's free, or costs gems we're allowed to spend."""
        b = self.bot.button(popup, name="orderFastForward")
        if b and (ui.is_free(b) or self.gems_ok(ui.gem_price(b), self.bot.snap)):
            return b
        return None

    async def press_skip(self, bid, b) -> bool:
        if not ui.is_free(b):
            return await self.pay_gems(b, f"skip order at {bid}")
        log.info("free skip at %s", bid)
        await self.bot.tap_xy(b["x"], b["y"])
        done = await self.bot.wait_for(lambda s: any(x["building"] == bid and x["state"] == DONE for x in s.orders), 2.5)
        return done is not None

    async def skip(self, bid, it) -> bool:
        b = self.skip_button(await self.bot.open_popup(it.cell, "Orders"))
        if b is None or not await self.press_skip(bid, b):
            self.bot.block(f"orders:{bid}", 20)
        await self.bot.close_popups()
        return True

    async def make(self, bid, it, o) -> bool:
        key = f"orders:{bid}"
        if await self.bot.open_popup(it.cell, "Orders") is None:
            self.bot.failed(key, limit=2, seconds=300)
            return True
        log.info("make %s at %s", o["recipe"].removeprefix("recipe_"), bid)
        if not await self.bot.press("Orders", name="orderMake"):
            self.bot.failed(key, limit=2, seconds=300)
            await self.bot.close_popups()
            return True
        started = await self.bot.wait_for(
            lambda s: any(x["building"] == bid and x["state"] not in (WAITING, READY) for x in s.orders), 3
        )
        if started is None:
            self.bot.failed(key, limit=2, seconds=300)
        else:
            self.bot.stats["orders made"] += 1
            # short recipes can be skipped for free right away (or for gems, when allowed)
            b = self.skip_button(await self.bot.stable_popup())
            if b is not None:
                await self.press_skip(bid, b)
        await self.bot.close_popups()
        return True
