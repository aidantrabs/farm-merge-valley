# fmvbot

Plays [Farm Merge Valley](https://discord.com/servers/farm-merge-valley-1158408201344135258) for you: the Discord Activity, or the web versions (CrazyGames, the standalone build).

Most bots for this game take screenshots and match item pictures, which breaks whenever the window
size, zoom, graphics or an event skin changes, and has to guess what is on the board. This one reads
the game's own state from inside the page (every item, timer, order, popup and inventory count,
exactly), works out what to do, and then plays with **real mouse input** sent through the browser's
DevTools protocol, the same way your mouse would. Nothing to calibrate, no template images, and it
keeps working in the background while you use your computer.

> Automating the game is against its terms of service. Your account could be banned. Use it at
> your own risk.

## What it does

| Job | Details |
| --- | --- |
| Merging | Plans whole merges: gathers 5 identical items into one group (5 make 2, 3 only make 1), moves or pushes other items out of the way, never lets an extra copy join by accident, prefers short drags. Falls back to 3s when the board is full. Coins, gems and energy only ever merge in 5s (3s gain nothing). |
| Supply crates | Opens crates while keeping room on the board for harvests and loot; opens more when the crate counter is capped so regeneration isn't wasted. |
| Harvesting | Taps grown crops and animals when they're ready, ingredients that open orders are waiting for first, then picks up the produce. Clears depleted plants and animals. |
| Orders | Starts each building's order once the ingredients are in, takes the free skip on short timers, claims the coins. |
| Workers | Sends workers to toolboxes, trees and rocks (toolboxes first, then whatever pending repairs need, cheapest step first), uses free skips, collects the wood, stone and tools. |
| Repairs | Discovers broken buildings and repairs them as soon as the materials are on the board. Those materials are reserved so they don't get merged away. |
| Land | Banks exactly the coins needed for the cheapest plot and buys it. |
| Reward chests | Opens chests when you have the keys and room for everything inside (making room if needed). Turns 5 keys into 2 of the next tier only when a better chest is waiting for them. |
| Free stuff | Discovery book gems, free timed marketplace gifts, daily challenge rewards, level-up and bonus popups. |
| Visits | Spends train tickets visiting farms, helps out there (train, shop, tree, animal, crop) and comes home. |
| Tutorial | Plays the onboarding on a brand-new farm by doing exactly what the tutorial asks for. |
| Upgrade cards | Uses them on their crop or animal. |
| Sticker packs | The sticker book (collector cards) opens over the whole game when packs arrive: presses "skip" on each reveal, collects the stickers and closes the book. |

Safety rails:

- Never spends gems or real money. Buttons that cost gems, show a price, or start an ad are never pressed.
- Never clicks outside the game itself, so Discord or the website around it are safe.
- Never presses sign-in, invite or share buttons.
- Doesn't change game data: everything happens through clicks and drags. The only thing it adjusts
  directly is where the camera looks, which is local to your screen.
- Human-like pointer movement and timing, an actions-per-minute cap, and optional breaks.

## Setup

Needs [uv](https://docs.astral.sh/uv/) (or any Python 3.12+).

```bash
uv sync
uv run playwright install chromium
```

## Running it

### Discord in the bot's own browser (easiest)

```bash
uv run fmvbot run
```

A Chromium window opens on Discord. The first time, log in (the login is remembered in the bot's
profile), then open Farm Merge Valley in any server, DM or voice channel. The bot starts as soon as
the game appears. This window keeps running at full speed even when it's in the background, and
once you're logged in you can hide it entirely with `--headless`. Add `--channel chrome` to use
your installed Google Chrome instead of the bundled Chromium.

### The Discord desktop app

```bash
uv run fmvbot discord            # restarts Discord with a local debugging port (9222)
# open Farm Merge Valley in Discord, then:
uv run fmvbot run --attach
```

`fmvbot discord` quits and reopens Discord with `--remote-debugging-port=9222` (macOS, Windows and
Linux). Keep the Discord window open (it can be behind other windows) so the game keeps drawing.
The port only listens on your own machine; restart Discord normally to turn it off.

### Chrome or another Chromium browser you already use

Start it with `--remote-debugging-port=9222`, open the game (Discord web, CrazyGames...), then
`uv run fmvbot run --attach`.

### Web versions

```bash
uv run fmvbot run --url https://www.crazygames.com/game/farm-merge-valley
uv run fmvbot run --lab          # the standalone build as a guest, handy for trying the bot out
```

If several games are open, pick one with `--frame-url TEXT` (part of the game frame's address).

### Other commands

```bash
uv run fmvbot status --attach    # what the bot sees: level, inventory, board, workers, popups
uv run fmvbot doctor --attach    # checks it can find the game and lists what it can read
uv run fmvbot run --minutes 60   # stop after an hour
```

Logs go to `logs/fmvbot.log`, with a screenshot whenever something goes wrong.

## Settings

Copy `fmvbot.example.toml` to `fmvbot.toml` and change what you like. Everything is optional; the
example file lists every setting with its default and what it does. The most useful ones:

- `tasks`: the jobs and their priority; remove one to turn it off (e.g. drop `"land"` to save coins).
- `prefer_merge5`, `merge3_when_free_below`: how patient merging is.
- `reserve_free_cells`: how much empty board to keep.
- `use_energy_on`, `energy_reserve`: where energy goes.
- `speed`, `max_actions_per_minute`, `break_every_minutes`: pacing.

## How it works

```
fmvbot/
  agent.js       injected into the game frame: finds the game's services, reports state and screen positions
  session.py     launches or attaches to the browser, finds the game frame (any page, any iframe)
  inputs.py      human-like mouse moves, taps and drags through the DevTools protocol
  game.py        snapshot and blueprint models
  planner.py     merge planning (pure, unit tested)
  bot.py         main loop: observe, let the highest-priority job act, verify, repeat
  tasks/         one module per job
```

The game is a PixiJS app bundled with webpack. `agent.js` borrows webpack's runtime to find Pixi,
briefly hooks the render loop to get hold of the scene, and from there finds the game's own
services: the grid, inventory, orders, workers, timers, camera, popups and the blueprint database
(every item, its merge chain and what it produces). Screen positions come from the game's isometric
projection and camera transform, and the press point for each object is chosen with the same hit
areas the game uses, so a tall building standing in front of an item can't steal the tap.

Every action is checked against a fresh snapshot (did the merge happen, did the crate count drop),
and anything that keeps failing is put aside for a while instead of being retried in a loop.

## Development

```bash
uv run pytest           # planner and button-safety tests
uv run ruff check .
uv run fmvbot run --lab -v
```

`--lab` opens the standalone web build as a throwaway guest farm, which is the easiest place to watch
the bot work.

## Known limits

- Built and tested against the standalone (v1.50) and CrazyGames web builds. The Discord build is
  the same game and code base, and everything is found by name and shape rather than fixed offsets,
  but if Discord's version renamed something, `fmvbot doctor` will show what couldn't be read.
- Visits couldn't be exercised end to end (the web builds have no train station), so that part is
  written defensively: it logs what the train station offers the first time and always returns home.
- The bot doesn't buy extra workers, premium land or anything else with gems.
