import argparse
import asyncio
import json
import logging
import shutil
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

from .bot import Bot
from .config import Config
from .session import GameNotFound, Session, default_profile_dir

DISCORD_URL = "https://discord.com/app"
LAB_URL = "https://cdn.games.mobinozer.com/ext/farm_merge_standalone/production/1.50.0/?publisherId=azerion"
DEFAULT_CDP = "http://127.0.0.1:9222"

log = logging.getLogger("fmvbot")


def setup_logging(verbose: bool, log_file: Path | None):
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file))
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname).1s %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
    )
    logging.getLogger("asyncio").setLevel(logging.WARNING)


async def open_session(args, cfg: Config) -> Session:
    s = Session(frame_filter=args.frame_url)
    await s.__aenter__()
    if args.attach:
        await s.attach(args.attach)
    else:
        url = args.url or (LAB_URL if args.lab else DISCORD_URL)
        await s.launch(
            url,
            Path(args.profile) if args.profile else default_profile_dir(),
            headless=args.headless or cfg.headless,
            channel=args.channel,
        )
        if args.lab:
            await _lab_login(s)
    return s


async def _lab_login(s: Session):
    """The standalone build asks how to log in; a guest save lives in the browser profile."""
    guest = s.context.pages[0].locator("#continue-as-guest")
    for _ in range(90):
        if await guest.is_visible():
            await guest.click()
            return
        await asyncio.sleep(1)


async def cmd_run(args, cfg: Config):
    s = await open_session(args, cfg)
    log_dir = Path(args.log_dir)
    bot = Bot(s, cfg, log_dir=log_dir)
    started = time.monotonic()
    try:
        await bot.run(max_minutes=args.minutes)
    finally:
        mins = (time.monotonic() - started) / 60
        log.info("stopped after %.1f min: %s", mins, ", ".join(f"{k}={v}" for k, v in sorted(bot.stats.items())))
        await s.close()


async def cmd_status(args, cfg: Config):
    s = await open_session(args, cfg)
    try:
        bot = Bot(s, cfg)
        await bot.connect()
        snap = await bot.observe()
        inv = snap.inv
        print(f"level {snap.level}  exp {snap.raw.get('exp')}/{snap.raw.get('nextExp')}")
        print("  ".join(f"{k} {inv.get(k, 0)}" for k in ("coins", "gems", "energy", "crates", "tickets")))
        skip = ("coins", "gems", "energy", "crates", "tickets", "level", "exp")
        produce = {k: v for k, v in inv.items() if v and k not in skip}
        if produce:
            print("inventory:", ", ".join(f"{k} {v}" for k, v in sorted(produce.items())))
        print(f"board: {len(snap.items)} objects, {len(snap.empty)} free cells, {snap.raw.get('locked')} locked")
        counts = Counter(it.id for it in snap.items)
        print("objects:", ", ".join(f"{k} x{v}" if v > 1 else k for k, v in counts.most_common()))
        print("workers:", snap.workers)
        if snap.tutorial.get("track"):
            print("tutorial:", snap.tutorial["track"])
        if snap.popups:
            print("popups:", [p["name"] for p in snap.popups])
        if args.json:
            print(json.dumps(snap.raw, indent=1))
    finally:
        await s.close()


async def cmd_doctor(args, cfg: Config):
    s = await open_session(args, cfg)
    try:
        pages = s._pages()
        print(f"{len(pages)} page(s):")
        for p in pages:
            print("  ", p.url[:100])
            for f in p.frames[1:]:
                print("     frame", f.url[:100])
        try:
            frame = await s.find_game(timeout=args.timeout)
        except GameNotFound as e:
            print("game: NOT FOUND -", e)
            return
        print("game frame:", frame.url[:120])
        info = await s.ensure_agent()
        print("scene:", info.get("sceneName"))
        print("global services:", ", ".join(info.get("globalServices", [])))
        print("scene services:", ", ".join(info.get("sceneServices", [])))
        print("frame origin:", await s.refresh_origin())
    finally:
        await s.close()


def cmd_discord(args):
    """Restart the Discord desktop app with a local debugging port the bot can attach to."""
    port = args.port
    if sys.platform == "darwin":
        subprocess.run(["osascript", "-e", 'quit app "Discord"'], check=False)
        time.sleep(3)
        subprocess.run(["open", "-a", "Discord", "--args", f"--remote-debugging-port={port}"], check=True)
    elif sys.platform == "win32":
        local = Path.home() / "AppData" / "Local" / "Discord"
        subprocess.run(["taskkill", "/IM", "Discord.exe", "/F"], check=False)
        time.sleep(3)
        subprocess.Popen(
            [
                str(local / "Update.exe"),
                "--processStart",
                "Discord.exe",
                "--process-start-args",
                f"--remote-debugging-port={port}",
            ]
        )
    else:
        exe = shutil.which("discord") or shutil.which("Discord")
        if not exe:
            sys.exit("could not find the discord executable on PATH")
        subprocess.run(["pkill", "-x", "Discord"], check=False)
        time.sleep(3)
        subprocess.Popen([exe, f"--remote-debugging-port={port}"])
    print(f"Discord restarted with --remote-debugging-port={port}.")
    print(f"Open Farm Merge Valley in Discord, then run:  fmvbot run --attach http://127.0.0.1:{port}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fmvbot", description="Plays Farm Merge Valley for you.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def conn(p):
        g = p.add_argument_group("where the game is")
        g.add_argument(
            "--attach",
            nargs="?",
            const=DEFAULT_CDP,
            metavar="CDP_URL",
            help=f"attach to a running Discord/Chrome with remote debugging (default {DEFAULT_CDP})",
        )
        g.add_argument("--url", help=f"page to open in the bot's own browser (default {DISCORD_URL})")
        g.add_argument("--lab", action="store_true", help="play the standalone web build as a guest (for testing)")
        g.add_argument("--profile", help="browser profile directory for the bot's own browser")
        g.add_argument("--headless", action="store_true", help="hide the bot's browser window")
        g.add_argument("--frame-url", metavar="TEXT", help="only use a game frame whose URL contains TEXT")
        g.add_argument("--channel", help="use an installed browser for the bot's window, e.g. chrome or msedge")
        p.add_argument("--config", type=Path, default=Path("fmvbot.toml"), help="settings file (default ./fmvbot.toml)")
        p.add_argument("-v", "--verbose", action="store_true")

    p = sub.add_parser("run", help="play the game")
    conn(p)
    p.add_argument("--minutes", type=float, default=0, help="stop after this long (default: run until Ctrl+C)")
    p.add_argument("--log-dir", default="logs", help="where to save logs and error screenshots")

    p = sub.add_parser("status", help="print what the bot sees")
    conn(p)
    p.add_argument("--json", action="store_true", help="dump the full snapshot")

    p = sub.add_parser("doctor", help="check that the bot can find and read the game")
    conn(p)
    p.add_argument("--timeout", type=float, default=20)

    p = sub.add_parser("discord", help="restart Discord desktop with a debugging port")
    p.add_argument("--port", type=int, default=9222)

    args = ap.parse_args(argv)
    if args.cmd == "discord":
        cmd_discord(args)
        return
    cfg = Config.load(args.config)
    log_file = Path(args.log_dir) / "fmvbot.log" if getattr(args, "log_dir", None) else None
    setup_logging(args.verbose, log_file)
    handler = {"run": cmd_run, "status": cmd_status, "doctor": cmd_doctor}[args.cmd]
    try:
        asyncio.run(handler(args, cfg))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
