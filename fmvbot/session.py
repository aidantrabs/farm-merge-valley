"""Connecting to a browser, locating the game frame and talking to the injected agent."""

import asyncio
import hashlib
import logging
import sys
from importlib import resources
from pathlib import Path

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Frame, Page, async_playwright

from .game import Snapshot

log = logging.getLogger(__name__)

_AGENT_SRC = resources.files("fmvbot").joinpath("agent.js").read_text()
AGENT_JS = _AGENT_SRC.replace("__AGENT_VERSION__", hashlib.sha1(_AGENT_SRC.encode()).hexdigest()[:12])

# frames whose URL contains one of these are checked first
FRAME_HINTS = ("discordsays.com", "mobinozer", "crazygames", "coolgames", "farm", "msn.com")

# the game's webpack global is webpackChunkfarm_merge (web) or webpackChunkfarm_merge_game (newer
# builds); Discord's own page also uses webpack and has canvases, so match the name
PROBE_JS = """() => Object.getOwnPropertyNames(self).some(k => /^webpackChunk.*(farm|merge)/i.test(k))
    && !!document.querySelector('canvas')"""

# keep the game running at full speed when the window is hidden or in the background
BACKGROUND_FLAGS = [
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    "--autoplay-policy=no-user-gesture-required",
]


class GameNotFound(RuntimeError):
    pass


class Session:
    def __init__(self, frame_filter: str | None = None):
        self.frame_filter = frame_filter
        self._pw = None
        self.browser = None
        self.context = None
        self.page: Page | None = None
        self.frame: Frame | None = None
        self._origin = (0.0, 0.0, 1.0)

    async def __aenter__(self):
        self._pw = await async_playwright().start()
        return self

    async def __aexit__(self, *exc):
        await self.close()

    async def close(self):
        # never close an attached browser (it is the user's Discord / Chrome)
        if self.context is not None and self.browser is None:
            try:
                await self.context.close()
            except PlaywrightError:
                pass
        if self._pw is not None:
            await self._pw.stop()
            self._pw = None

    async def attach(self, cdp_url: str):
        log.info("attaching to %s", cdp_url)
        self.browser = await self._pw.chromium.connect_over_cdp(cdp_url)

    async def launch(self, url: str, profile: Path, headless: bool = False, channel: str | None = None):
        profile.mkdir(parents=True, exist_ok=True)
        log.info("launching browser (profile %s)", profile)
        self.context = await self._pw.chromium.launch_persistent_context(
            str(profile),
            headless=headless,
            viewport=None if not headless else {"width": 1440, "height": 900},
            args=BACKGROUND_FLAGS
            + ["--disable-blink-features=AutomationControlled"]
            + ([] if headless else ["--start-maximized"]),
            ignore_default_args=["--enable-automation"],
            channel=channel,
        )
        page = self.context.pages[0] if self.context.pages else await self.context.new_page()
        if page.url in ("about:blank", "") or not page.url.startswith(url.split("?")[0][:30]):
            await page.goto(url)

    def _pages(self) -> list[Page]:
        if self.browser is not None:
            return [p for c in self.browser.contexts for p in c.pages]
        return list(self.context.pages) if self.context else []

    async def find_game(self, timeout: float = 0, poll: float = 2.0) -> Frame:
        """Search every page and iframe for the game. timeout=0 waits forever."""
        loop = asyncio.get_running_loop()
        end = loop.time() + timeout if timeout else None
        announced = False
        while True:
            frame = await self._probe_frames()
            if frame is not None:
                self.frame = frame
                self.page = frame.page
                log.info("game frame found: %s", frame.url[:100])
                return frame
            if end is not None and loop.time() > end:
                raise GameNotFound("Farm Merge Valley is not open in any tab or Discord window")
            if not announced:
                log.info("waiting for Farm Merge Valley to be opened...")
                announced = True
            await asyncio.sleep(poll)

    async def _probe_frames(self) -> Frame | None:
        frames = [f for p in self._pages() for f in p.frames]
        if self.frame_filter:
            frames = [f for f in frames if self.frame_filter in f.url]
        frames.sort(key=lambda f: not any(h in f.url for h in FRAME_HINTS))
        for f in frames:
            if f.is_detached():
                continue
            try:
                if await asyncio.wait_for(f.evaluate(PROBE_JS), 3):
                    return f
            except (TimeoutError, PlaywrightError):
                continue
        return None

    # ------------------------------------------------------------------ agent

    async def eval(self, expr: str, arg=None):
        if self.frame is None or self.frame.is_detached():
            raise GameNotFound("game frame is gone")
        return await self.frame.evaluate(expr, arg)

    async def ensure_agent(self) -> dict:
        await self.eval(AGENT_JS)
        return await self.eval("() => window.__fmvAgent.init()")

    async def snapshot(self) -> Snapshot:
        raw = await self.eval("() => window.__fmvAgent ? window.__fmvAgent.snapshot() : null")
        if raw is None:
            await self.ensure_agent()
            raw = await self.eval("() => window.__fmvAgent.snapshot()")
        return Snapshot(raw)

    async def blueprints(self) -> dict:
        return await self.eval("() => window.__fmvAgent.blueprints()")

    async def recipes(self) -> dict:
        return await self.eval("() => window.__fmvAgent.recipes()")

    async def cell_css(self, c: int, r: int):
        return await self.eval("([c, r]) => window.__fmvAgent.cellCss(c, r)", [c, r])

    async def pick_point(self, c: int, r: int):
        return await self.eval("([c, r]) => window.__fmvAgent.pickPoint(c, r)", [c, r])

    async def camera(self):
        return await self.eval("() => window.__fmvAgent.camera()")

    async def focus_cell(self, c: int, r: int) -> bool:
        return await self.eval("([c, r]) => window.__fmvAgent.focusCell(c, r)", [c, r])

    # ------------------------------------------------------------- geometry

    async def refresh_origin(self):
        """Where the game frame's (0,0) sits in page coordinates, and its CSS scale."""
        if self.frame is None or self.frame == self.page.main_frame:
            self._origin = (0.0, 0.0, 1.0)
            return self._origin
        el = await self.frame.frame_element()
        box = await el.bounding_box()
        inner_w = await self.frame.evaluate("() => innerWidth")
        border = await el.evaluate(
            "e => { const s = getComputedStyle(e);"
            " return [e.clientLeft + parseFloat(s.paddingLeft || 0), e.clientTop + parseFloat(s.paddingTop || 0)] }"
        )
        scale = (box["width"] - 2 * border[0]) / inner_w if inner_w else 1.0
        self._origin = (box["x"] + border[0], box["y"] + border[1], scale or 1.0)
        return self._origin

    def to_page(self, x: float, y: float) -> tuple[float, float]:
        ox, oy, s = self._origin
        return ox + x * s, oy + y * s

    async def game_on_top(self, px: float, py: float) -> bool:
        """Is the game what's actually under this page point? Discord draws its own controls over
        activities, and web portals put ads and dialogs over theirs; a press there must not happen."""
        chain = []
        f = self.frame
        while f is not None:
            chain.append(f)
            f = f.parent_frame
        chain.reverse()  # main frame ... game frame
        for outer, inner in zip(chain, chain[1:], strict=False):
            el = await inner.frame_element()
            if outer.parent_frame is None:
                ox, oy = 0.0, 0.0
            else:
                box = await (await outer.frame_element()).bounding_box()
                ox, oy = (box["x"], box["y"]) if box else (0.0, 0.0)
            on_top = await outer.evaluate(
                "([x, y, el]) => { const e = document.elementFromPoint(x, y); return e === el; }", [px - ox, py - oy, el]
            )
            if not on_top:
                return False
        ox, oy, scale = self._origin
        return await self.frame.evaluate(
            "([x, y]) => { const e = document.elementFromPoint(x, y); return !!e && e.tagName === 'CANVAS'; }",
            [(px - ox) / scale, (py - oy) / scale],
        )


def default_profile_dir() -> Path:
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif sys.platform == "win32":
        base = Path.home() / "AppData" / "Local"
    else:
        base = Path.home() / ".local" / "share"
    return base / "fmvbot" / "browser-profile"
