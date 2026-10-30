#!/usr/bin/env python3
"""Play with the monitoring site: serve invented triggers so you can open the page and click around.

It is the real MonitorSite over a real TriggerHistory; only the triggers are made up (invented lines, not anything said in a
room), together with an invented status (a bar, a window, which servers are up). Open the address it prints, with any user
name and the password you gave (the default is `demo`).

Usage:
    serve_fake_triggers.py                    # serve on port 8080 with the password "demo"
    serve_fake_triggers.py --live 15          # add a new invented trigger every 15 seconds, to watch the page update itself
    serve_fake_triggers.py --hide-hinges      # the page with the hinge text hidden (numbers only)
    serve_fake_triggers.py --port 9000 --password hunter2
    serve_fake_triggers.py --check            # serve on a free port, read the page's JSON feed once, and exit

ctrl-C quits.
"""
import argparse
import base64
import itertools
import json
import random
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
from pom.corpus import Chunk                                                  # noqa: E402
from pom.monitor import MonitorSite, MonitorStatus, TriggerHistory            # noqa: E402
from pom.voice import Portent                                                 # noqa: E402

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
    Chunk("dreambook-demo#00001", "dreambook-demo", "divination", "To dream of a long staircase is a sign of a journey not yet begun.", 14),
    Chunk("horoscope-demo#00002", "horoscope-demo", "horoscope", "With the Moon in the house of letters, an answer arrives sealed.", 12),
    Chunk("aphorism-demo#00003", "aphorism-demo", "wisdom", "What is not yours is another man's weather; let him look to it.", 13),
]
PORTENTS = [
    "A letter will come under the door, and you will not open it. This is the correct decision.",
    "The river forgets its name each evening and is pleased to be asked again.",
    "Counting is a kind of prayer. The ships know this and stay away.",
]


def fake_trigger(history: TriggerHistory, rng: random.Random, n: int) -> None:
    """Adds one invented trigger; every fourth one has no portent, to see that page too."""
    exemplars = tuple(rng.sample(EXEMPLARS, 1))
    portent = None if n % 4 == 3 else Portent(rng.choice(PORTENTS), exemplars, rng.uniform(0.4, 38.0), n % 5 == 4)
    history.add(HINGES[n % len(HINGES)], round(rng.uniform(6.5, 9.0), 2), round(rng.uniform(6.0, 7.5), 2), rng.randint(60, 300),
                float(n * 190), portent)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--port", type=int, default=8080, help="the port to serve on")
    parser.add_argument("--password", default="demo", help="the password for the page")
    parser.add_argument("--hide-hinges", action="store_true", help="hide the hinge text, as show_hinges = false does")
    parser.add_argument("--live", type=float, default=0.0, metavar="SECONDS", help="add a new invented trigger this often")
    parser.add_argument("--check", action="store_true", help="serve on a free port, read the JSON feed once, and exit")
    args = parser.parse_args()

    rng = random.Random(7)
    history = TriggerHistory()
    started = time.time()
    for n in range(5):
        fake_trigger(history, rng, n)
    site = MonitorSite(history, args.password, show_hinges=not args.hide_hinges,
                       status_source=lambda: MonitorStatus(bar=6.84, window_size=118, sentences_heard=1432, sentences_scored=1391,
                                                           services={"whisper": True, "scorer": True, "oracle": True, "typewriter": False}))
    if not site.start("127.0.0.1" if args.check else "0.0.0.0", 0 if args.check else args.port):
        sys.exit("the site did not start: " + "; ".join(site.report.lines()))

    if args.check:
        request = urllib.request.Request(f"http://127.0.0.1:{site.port}/api/triggers", headers={
            "Authorization": "Basic " + base64.b64encode(f"x:{args.password}".encode()).decode()})
        with urllib.request.urlopen(request, timeout=10) as response:
            feed = json.load(response)
        print(f"the site answered: {len(feed['triggers'])} triggers, bar {feed['stats']['bar_text']}")
        site.stop()
        return

    print(f"serving {len(history.recent())} invented triggers: open http://localhost:{site.port}  (any user name, password: {args.password})")
    print("on the same network: http://<this machine's name or address>:" + str(site.port))
    print("ctrl-C quits.", flush=True)
    try:
        for n in itertools.count(5):
            if args.live > 0:
                time.sleep(args.live)
                fake_trigger(history, rng, n)
            else:
                time.sleep(3600)
    except KeyboardInterrupt:
        print()
    finally:
        site.stop()


if __name__ == "__main__":
    main()
