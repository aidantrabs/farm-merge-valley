import asyncio
import re
import time

from .. import ui
from . import Task, log

SKIP = re.compile(r"\bskip\b", re.I)


class StickersTask(Task):
    """The sticker book (collector cards) opens over the whole game when packs arrive: skip each
    pack's reveal, collect the stickers, then close the book so the farm can be played again."""

    name = "stickers"

    def __init__(self, bot):
        super().__init__(bot)
        self.since: float | None = None
        self.pressed = 0.0

    async def tap(self, x: float, y: float):
        await self.bot.tap_xy(x, y)
        self.pressed = time.monotonic()

    async def step(self, snap) -> bool:
        if not snap.overlays:
            self.since = None
            return False
        now = time.monotonic()
        if self.since is None:
            self.since = self.pressed = now
            log.info("sticker book is open")
        ov = snap.overlays[-1]
        view = snap.view
        buttons = [b for b in ov["ui"] if snap.on_screen(b["x"], b["y"]) and b["w"] < view["w"] * 0.9]
        texts = [t for t in ov.get("texts") or [] if snap.on_screen(t["x"], t["y"])]

        # "Press to skip" sits in the top right while a pack reveals its stickers
        skip = next((b for b in buttons if SKIP.search(ui.label(b))), None) or next(
            (t for t in texts if SKIP.search(t["text"])), None
        )
        if skip is not None:
            log.debug("stickers: skip")
            await self.tap(skip["x"], skip["y"])
            await asyncio.sleep(0.8)
            return True
        good = [b for b in buttons if ui.is_positive(b)]
        if good:
            b = ui.biggest(good)
            log.info("stickers: %s", (b["text"] or b["name"]).lower() or "collect")
            await self.tap(b["x"], b["y"])
            self.bot.stats["sticker packs"] += 1
            await asyncio.sleep(1.0)
            return True
        close = ui.close_button(buttons)
        if close is not None and now - self.since > 2:
            log.info("closing the sticker book")
            await self.tap(close["x"], close["y"])
            await self.bot.wait_for(lambda s: not s.overlays, 3)
            return True
        if now - self.pressed > 20:
            # celebrations (a set or the album completed) may wait for a tap anywhere
            log.info("sticker book: nothing to press (%s); tapping the middle", "; ".join(t["text"] for t in texts)[:120])
            await self.bot._screenshot("stickers")
            await self.tap(view["l"] + view["w"] / 2, view["t"] + view["h"] * 0.6)
            return True
        # still animating
        await asyncio.sleep(0.5)
        return True
