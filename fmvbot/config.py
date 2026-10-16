"""Bot settings. Defaults live here; a TOML file overrides any of them."""

import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path


@dataclass
class Config:
    # which jobs the bot does, in priority order (remove names to disable them).
    # merge and crates always find work, so everything occasional goes before them.
    tasks: list[str] = field(
        default_factory=lambda: [
            "stickers",
            "tutorial",
            "popups",
            "collect",
            "harvest",
            "clear_depleted",
            "orders",
            "workers",
            "chests",
            "repair",
            "land",
            "rewards",
            "visits",
            "cards",
            "merge",
            "crates",
            "tidy",
        ]
    )

    # board space
    reserve_free_cells: int = 5  # keep this many empty cells after opening crates (room for harvests and loot)
    min_free_cells: int = 3  # below this, bank low-value coins to make room
    crate_burst: int = 6  # crates opened per turn before re-planning merges

    # merging
    prefer_merge5: bool = True  # wait for 5 identical items (2 results instead of 1)
    merge3_when_free_below: int = 6  # ...unless the board is this full
    merge3_always: list[str] = field(default_factory=lambda: ["wood", "stone", "tool"])
    merge5_only: list[str] = field(default_factory=lambda: ["coin", "gem", "energy"])
    never_merge: list[str] = field(default_factory=list)  # chests and keys are always left to the chests task
    max_moves_per_merge: int = 6

    # currencies lying on the board
    collect_coins_at_tier: int = 8  # bank coin items once they reach this tier
    collect_gems_at_tier: int = 0  # 0 = never pick up gem items, only merge them
    keep_energy_items: bool = True  # use energy items only when energy runs low
    energy_low: int = 10

    # gems from the inventory (never real money) can skip timers; off unless turned on
    spend_gems: bool = False
    gem_reserve: int = 0  # never go below this many gems
    max_gems_per_skip: int = 0  # 0 = no limit

    # land (bought with coins only)
    unlock_land: bool = True
    land_coin_reserve: int = 0

    # workers / energy
    use_energy_on: list[str] = field(default_factory=lambda: ["toolbox", "tree", "rock"])
    energy_reserve: int = 0

    # free marketplace items on web builds play a video ad first; Discord has no ads
    watch_ads: bool = False

    # visits with train tickets
    visit_friends: bool = True

    # pacing
    speed: float = 1.0  # >1 is faster, <1 is slower
    idle_poll: float = 20.0  # seconds between checks when nothing to do
    max_actions_per_minute: int = 90
    break_every_minutes: float = 0  # 0 = never take breaks
    break_minutes: float = 3

    # misc
    headless: bool = False
    screenshots_on_error: bool = True

    @classmethod
    def load(cls, path: Path | None) -> "Config":
        cfg = cls()
        if path is None or not path.exists():
            return cfg
        data = tomllib.loads(path.read_text())
        known = {f.name for f in fields(cls)}
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"unknown settings in {path}: {', '.join(sorted(unknown))}")
        for k, v in data.items():
            setattr(cfg, k, v)
        return cfg
