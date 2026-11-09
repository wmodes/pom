#!/usr/bin/env python3
"""Play with the monitoring site: serve invented turns so you can open the page and click around.

It is the real MonitorSite over a real TurnHistory; only the turns are made up (invented lines, not anything said in a
room), together with an invented status (a bar, a window, which servers are up). Open the address it prints, with any user
name and the password you gave (the default is `demo`).

Usage:
    serve_fake_turns.py                    # serve on port 8080 with the password "demo"
    serve_fake_turns.py --live 15          # add a new invented turn every 15 seconds, to watch the page update itself
    serve_fake_turns.py --terminal down    # the terminal's light: up, down or none (the default)
    serve_fake_turns.py --hide-hinges      # the page with the hinge text hidden (numbers only)
    serve_fake_turns.py --port 9000 --password hunter2
    serve_fake_turns.py --chart            # also draw the chart: the real BarKeeper and anchor (from config/pom.toml) over invented attempts
    serve_fake_turns.py --chart --quiet-start 12  # ...and nothing is heard for the first 12 stream minutes
    serve_fake_turns.py --chart --speed 60 # ...with the stream clock running 60 times faster than the wall clock (default 30)
    serve_fake_turns.py --check            # serve on a free port, read the page's JSON feed once, and exit

ctrl-C quits.
"""
import argparse
import base64
import itertools
import json
import random
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
from pom.config import Config                                                     # noqa: E402
from pom.library import Passage                                                  # noqa: E402
from pom.monitor import MonitorSite, MonitorStatus, ChartNotebook, TurnHistory, silence_shape  # noqa: E402
from pom.qualifying import Ruling, bar_keeper_from_settings               # noqa: E402
from pom.writing import Portent                                                 # noqa: E402

HINGES = [
    "The lighthouse keeper counts the ships that never arrive.",
    "My grandmother kept her teeth in a jar by the stove.",
    "The committee voted to rename the river.",
    "<script>alert('this is only text')</script> & other things people say",
    "Nooooooo.",
    "We could paint the whole ceiling the colour of weather.",
    "She said the elevator had been going up for three days.",
]
EXEMPLARS = [
    Passage("dreambook-demo#00001", "dreambook-demo", "divination", "To dream of a long staircase is a sign of a journey not yet begun.", 14),
    Passage("horoscope-demo#00002", "horoscope-demo", "horoscope", "With the Moon in the house of letters, an answer arrives sealed.", 12),
    Passage("aphorism-demo#00003", "aphorism-demo", "wisdom", "What is not yours is another man's weather; let him look to it.", 13),
]
PORTENTS = [
    "A letter will come under the door, and you will not open it. This is the correct decision.",
    "The river forgets its name each evening and is pleased to be asked again.",
    "Counting is a kind of prayer. The ships know this and stay away.",
]


def fake_turn(history: TurnHistory, rng: random.Random, n: int) -> None:
    """Adds one invented turn; every fourth one has no portent, to see that page too."""
    exemplars = tuple(rng.sample(EXEMPLARS, 1))
    portent = None if n % 4 == 3 else Portent(rng.choice(PORTENTS), exemplars, rng.uniform(0.4, 38.0), n % 5 == 4)
    history.add(HINGES[n % len(HINGES)], round(rng.uniform(6.5, 9.0), 2), round(rng.uniform(6.0, 7.5), 2), rng.randint(60, 300),
                float(n * 190), portent)


class InventedRoom:
    """The real BarKeeper and anchor, fed invented attempts on a stream clock that runs `speed` times faster than the wall clock.

    Attempts come in bursts with quiet stretches between, so the bar can be watched rising, holding and sliding down through silence. The first
    `head_start_minutes` of stream time are played at once, so the chart is not empty when the page is opened.
    """

    def __init__(self, config: Config, history: TurnHistory, rng: random.Random, speed: float, head_start_minutes: float = 35.0,
                 quiet_start_minutes: float = 0.0):
        self.keeper = bar_keeper_from_settings(config.qualifying, 0.0)
        self.notebook = ChartNotebook(config.chart.window_minutes * 60.0, config.monitor.chart_notebook_max_entries, ticks_source=self.keeper.ticks_between,
                                      silence_shape=silence_shape(config.chart), began_at=0.0)
        self.history, self.rng, self.speed = history, rng, speed
        self.window_minutes = config.chart.window_minutes
        self._next_at, self._quiet_until, self._turns = max(20.0, quiet_start_minutes * 60.0), 0.0, 0         # the room is quiet until the first attempt
        self._begun = time.monotonic() - head_start_minutes * 60.0 / speed
        self.heard = self.scored = 0
        self.advance_to(self.now())

    def now(self) -> float:
        return (time.monotonic() - self._begun) * self.speed

    def advance_to(self, now: float) -> None:
        """Plays every attempt due up to stream time `now`."""
        while self._next_at <= now:
            at = self._next_at
            score = max(0.0, min(9.0, self.rng.gauss(5.8, 0.9) + (2.2 if self.rng.random() < 0.04 else 0.0)))
            bar = self.keeper.bar_at(at)
            ruling = self.keeper.rule(score, at)
            self.heard += 1
            self.scored += 1
            qualified = ruling is Ruling.QUALIFIED
            self.notebook.add(score, bar, at, qualified)
            if qualified:
                self.history.add(HINGES[self._turns % len(HINGES)], round(score, 2), round(bar, 2) if bar is not None else None,
                                 self.keeper.window_size_at(at), at, None)
                self._turns += 1
            gap = self.rng.uniform(15.0, 70.0)
            if self.rng.random() < 0.04:                       # fairly often the room goes quiet for a good while
                gap += self.rng.uniform(6.0, 25.0) * 60.0
            self._next_at = at + gap

    def run(self) -> None:
        while True:
            time.sleep(0.25)
            self.advance_to(self.now())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--port", type=int, default=8080, help="the port to serve on")
    parser.add_argument("--password", default="demo", help="the password for the page")
    parser.add_argument("--hide-hinges", action="store_true", help="hide the hinge text, as show_hinges = false does")
    parser.add_argument("--live", type=float, default=0.0, metavar="SECONDS", help="add a new invented turn this often")
    parser.add_argument("--terminal", choices=["up", "down", "none"], default="none", help="what the terminal's light says on the page")
    parser.add_argument("--chart", action="store_true", help="draw the chart from the real BarKeeper over invented attempts")
    parser.add_argument("--quiet-start", type=float, default=0.0, metavar="MINUTES", help="with --chart, nothing is heard for this many stream minutes")
    parser.add_argument("--speed", type=float, default=30.0, help="with --chart, how many times faster than the wall clock the stream runs")
    parser.add_argument("--check", action="store_true", help="serve on a free port, read the JSON feed once, and exit")
    args = parser.parse_args()

    rng = random.Random(7)
    history = TurnHistory()
    started = time.time()
    for n in range(5):
        fake_turn(history, rng, n)
    services = {"whisper": True, "scorer": True, "oracle": True, "terminal": {"up": True, "down": False, "none": None}[args.terminal]}
    if args.chart:
        config = Config.load(ROOT / "config" / "pom.toml")
        room = InventedRoom(config, history, rng, args.speed, quiet_start_minutes=args.quiet_start)
        threading.Thread(target=room.run, daemon=True).start()
        site = MonitorSite(history, args.password, show_hinges=not args.hide_hinges, notebook=room.notebook, chart=config.chart,
                           poll_seconds=config.monitor.poll_seconds, hinge_snippet_chars=config.monitor.hinge_snippet_chars, stream_now=room.now, stream_began_at=0.0,
                           status_source=lambda: MonitorStatus(bar=room.keeper.bar_at(room.now()), window_size=room.keeper.window_size_at(room.now()),
                                                               attempts_heard=room.heard, attempts_scored=room.scored, services=services,
                                                               algorithm=config.qualifying.algorithm, bar_description=room.keeper.describe()))
    else:
        site = MonitorSite(history, args.password, show_hinges=not args.hide_hinges,
                           status_source=lambda: MonitorStatus(bar=6.84, window_size=118, attempts_heard=1432, attempts_scored=1391, services=services))
    if not site.start("127.0.0.1" if args.check else "0.0.0.0", 0 if args.check else args.port):
        sys.exit("the site did not start: " + "; ".join(site.report.lines()))

    if args.check:
        request = urllib.request.Request(f"http://127.0.0.1:{site.port}/api/turns", headers={
            "Authorization": "Basic " + base64.b64encode(f"x:{args.password}".encode()).decode()})
        with urllib.request.urlopen(request, timeout=10) as response:
            feed = json.load(response)
        light = {True: "up", False: "down", None: "none"}[feed["stats"]["services"]["terminal"]]
        print(f"the site answered: {len(feed['turns'])} turns, bar {feed['stats']['bar_text']}, "
              f"{feed['stats']['attempts_heard']} attempts heard, terminal light {light}")
        site.stop()
        return

    print(f"serving {len(history.recent())} invented turns: open http://localhost:{site.port}  (any user name, password: {args.password})")
    print("on the same network: http://<this machine's name or address>:" + str(site.port))
    print("ctrl-C quits.", flush=True)
    try:
        for n in itertools.count(5):
            if args.live > 0:
                time.sleep(args.live)
                fake_turn(history, rng, n)
            else:
                time.sleep(3600)
    except KeyboardInterrupt:
        print()
    finally:
        site.stop()


if __name__ == "__main__":
    main()
