import asyncio
from collections import Counter

from fmvbot.config import Config
from fmvbot.game import Snapshot
from fmvbot.tasks.stickers import StickersTask


class FakeBot:
    def __init__(self):
        self.cfg = Config()
        self.taps = []
        self.stats = Counter()

    async def tap_xy(self, x, y, jitter=2.0):
        self.taps.append((x, y))

    async def wait_for(self, pred, timeout=3.0, interval=0.25):
        return None

    async def _screenshot(self, tag):
        pass


def overlay_snap(ui, texts=()):
    return Snapshot(
        {
            "ok": True,
            "view": {"l": 0, "t": 0, "w": 904, "h": 407},
            "overlays": [{"id": 1, "name": "_0x353878", "ui": list(ui), "texts": list(texts)}],
        }
    )


def el(name, text, tex, x, y, w=90, h=22):
    return {"name": name, "text": text, "tex": tex, "x": x, "y": y, "w": w, "h": h}


def test_sticker_book_skips_then_collects_then_closes(monkeypatch):
    async def no_wait(_):
        pass

    monkeypatch.setattr("fmvbot.tasks.stickers.asyncio.sleep", no_wait)
    blocker = el("", "", [], 452, 204, 904, 407)
    skip = el("", "Press to skip", ["pixiid_1040"], 854, 70)
    collect = el("ConsentButton", "Collect", ["btn_event_green_idle"], 452, 361, 116, 30)
    close = el("", "", ["btn_close_idle"], 887, 32, 17, 17)
    album = el("", "6 / 9", ["bg_stickerset_amountpill"], 191, 173, 79, 110)
    bot = FakeBot()
    task = StickersTask(bot)
    asyncio.run(task.step(overlay_snap([blocker, skip], [{"text": "Press to skip", "x": 854, "y": 70}])))
    asyncio.run(task.step(overlay_snap([blocker, collect])))
    task.since -= 5
    asyncio.run(task.step(overlay_snap([blocker, album, close])))
    assert bot.taps == [(854, 70), (452, 361), (887, 32)]
    assert not asyncio.run(task.step(Snapshot({"ok": True})))
