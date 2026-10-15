"""Real mouse input through the browser (CDP), shaped to look like a person using a mouse."""

import asyncio
import math
import random

from .session import Session


class Pointer:
    def __init__(self, session: Session, speed: float = 1.0, jitter: float = 3.0, seed: int | None = None):
        self.s = session
        self.speed = max(0.25, speed)
        self.jitter = jitter
        self.rng = random.Random(seed)
        self.pos: tuple[float, float] | None = None

    def _t(self, lo: float, hi: float) -> float:
        return self.rng.uniform(lo, hi) / self.speed

    def _jit(self, x: float, y: float, amount: float | None = None) -> tuple[float, float]:
        a = self.jitter if amount is None else amount
        return x + self.rng.uniform(-a, a), y + self.rng.uniform(-a, a)

    async def _move(self, x: float, y: float, duration: float, curve: float = 0.15):
        mouse = self.s.page.mouse
        px, py = self.s.to_page(x, y)
        if self.pos is None:
            await mouse.move(px, py)
            self.pos = (px, py)
            return
        sx, sy = self.pos
        dist = math.hypot(px - sx, py - sy)
        steps = max(2, min(30, int(dist / 18)))
        # bend the path a little, like a wrist movement
        nx, ny = -(py - sy), px - sx
        norm = math.hypot(nx, ny) or 1.0
        bend = self.rng.uniform(-curve, curve) * dist
        cx, cy = (sx + px) / 2 + nx / norm * bend, (sy + py) / 2 + ny / norm * bend
        for i in range(1, steps + 1):
            t = i / steps
            e = t * t * (3 - 2 * t)
            bx = (1 - e) ** 2 * sx + 2 * (1 - e) * e * cx + e * e * px
            by = (1 - e) ** 2 * sy + 2 * (1 - e) * e * cy + e * e * py
            await mouse.move(bx, by)
            await asyncio.sleep(duration / steps)
        self.pos = (px, py)

    async def tap(self, x: float, y: float, jitter: float | None = None):
        x, y = self._jit(x, y, jitter)
        await self._move(x, y, self._t(0.08, 0.22))
        await asyncio.sleep(self._t(0.03, 0.09))
        await self.s.page.mouse.down()
        await asyncio.sleep(self._t(0.05, 0.12))
        await self.s.page.mouse.up()
        await asyncio.sleep(self._t(0.08, 0.18))

    async def drag(self, x1: float, y1: float, x2: float, y2: float, jitter: float | None = None):
        mouse = self.s.page.mouse
        x1, y1 = self._jit(x1, y1, jitter)
        x2, y2 = self._jit(x2, y2, jitter)
        await self._move(x1, y1, self._t(0.08, 0.2))
        await asyncio.sleep(self._t(0.04, 0.1))
        await mouse.down()
        await asyncio.sleep(self._t(0.08, 0.16))
        # nudge past the game's drag threshold before travelling
        await self._move(x1 + (x2 - x1) * 0.04, y1 + (y2 - y1) * 0.04 + 2, self._t(0.04, 0.08), curve=0)
        dist = math.hypot(x2 - x1, y2 - y1)
        await self._move(x2, y2, self._t(0.18, 0.32) + dist / 2500, curve=0.08)
        await asyncio.sleep(self._t(0.08, 0.16))
        await mouse.up()
        await asyncio.sleep(self._t(0.12, 0.25))

    async def pause(self, lo: float, hi: float):
        await asyncio.sleep(self._t(lo, hi))
