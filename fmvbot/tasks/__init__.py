import logging

from .. import ui
from ..game import Snapshot

log = logging.getLogger("fmvbot")


class Task:
    name = ""

    def __init__(self, bot):
        self.bot = bot
        self.cfg = bot.cfg

    @property
    def bp(self):
        return self.bot.bp

    async def step(self, snap: Snapshot) -> bool:
        """Do at most one unit of work. Return True if anything was attempted."""
        raise NotImplementedError

    def gems_ok(self, price: int | None, snap: Snapshot) -> bool:
        """May a timer be skipped for this many gems?"""
        cfg = self.cfg
        if not cfg.spend_gems or price is None:
            return False
        if cfg.max_gems_per_skip and price > cfg.max_gems_per_skip:
            return False
        return snap.amount("gems") - price >= cfg.gem_reserve

    async def pay_gems(self, b: dict, what: str) -> bool:
        """Press a gem-priced button and confirm it. True once the gems are actually gone."""
        bot = self.bot
        before = bot.snap.amount("gems")
        log.info("%s for %d gems", what, ui.gem_price(b))
        await bot.tap_xy(b["x"], b["y"])

        def confirming(s):
            # FastForwardConfirmationPopup for skips, PurchaseConfirmationPopup in the shop
            return s.top_popup and "Confirmation" in s.top_popup["name"]

        snap = await bot.wait_for(lambda s: s.amount("gems") < before or confirming(s), 2.5)
        if snap is not None and confirming(snap):
            p = await bot.stable_popup()
            # the button showing the price (the panel around it shows the price too, but it's bigger)
            priced = [x for x in (p or {}).get("ui", []) if ui.gem_price(x) is not None and not ui.is_close(x)]
            ok = min(priced, key=lambda x: x["w"] * x["h"], default=None)
            if ok is None or not self.gems_ok(ui.gem_price(ok), snap):
                # the price changed or isn't gems after all: back out of this dialog only
                close = bot.button(p, name="close")
                if close is not None:
                    await bot.tap_xy(close["x"], close["y"])
                return False
            await bot.tap_xy(ok["x"], ok["y"])
            snap = await bot.wait_for(lambda s: s.amount("gems") < before, 2.5)
        if snap is None or snap.amount("gems") >= before:
            return False
        bot.stats["gems spent"] += before - snap.amount("gems")
        return True

    async def skip_cooldown(self, it, what: str) -> bool:
        """Tap something that's counting down (a worker's job, a crop regrowing) and skip the wait:
        for free when the game offers it, otherwise for gems when that's allowed."""
        bot = self.bot

        def button(s):
            return bot._menu_button(s, it, lambda b: ui.is_free(b) or b["name"] == "cooldownPurchase")

        for attempt in range(2):
            if attempt:
                # the menu opened past the edge of the screen: bring the object to the middle
                await bot.dismiss_popouts()
                await bot.s.focus_cell(*it.cell)
                await bot.settle()
            if not await bot.tap_cell(it.cell):
                return False
            snap = await bot.wait_for(lambda s: button(s) is not None, 1.5)
            if snap is None:
                return False
            b = button(snap)
            if bot._inside(b["x"], b["y"], quiet=True):
                break
        else:
            return False
        if ui.is_free(b):
            log.info("free skip on %s", what)
            await bot.tap_xy(b["x"], b["y"])
            return True
        if self.gems_ok(ui.gem_price(b), snap):
            return await self.pay_gems(b, f"skip {what}")
        await bot.dismiss_popouts()
        return False


def build_tasks(bot, names: list[str]) -> list[Task]:
    from .board import ClearDepletedTask, CollectTask, CratesTask, HarvestTask, TidyTask
    from .chests import ChestsTask
    from .merging import MergeCoinsTask, MergeTask
    from .orders import OrdersTask
    from .popups import PopupTask
    from .progress import CardsTask, LandTask, RepairTask
    from .rewards import RewardsTask, VisitsTask
    from .stickers import StickersTask
    from .tutorial import TutorialTask
    from .workers import WorkersTask

    registry = {
        t.name: t
        for t in (
            PopupTask,
            StickersTask,
            TutorialTask,
            CollectTask,
            HarvestTask,
            ClearDepletedTask,
            OrdersTask,
            WorkersTask,
            ChestsTask,
            MergeTask,
            MergeCoinsTask,
            CratesTask,
            RepairTask,
            LandTask,
            RewardsTask,
            VisitsTask,
            CardsTask,
            TidyTask,
        )
    }
    unknown = [n for n in names if n not in registry]
    if unknown:
        raise ValueError(f"unknown tasks: {', '.join(unknown)} (known: {', '.join(registry)})")
    return [registry[n](bot) for n in names]
