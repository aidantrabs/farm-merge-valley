import logging

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


def build_tasks(bot, names: list[str]) -> list[Task]:
    from .board import ClearDepletedTask, CollectTask, CratesTask, HarvestTask, TidyTask
    from .chests import ChestsTask
    from .merging import MergeTask
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
