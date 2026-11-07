#!/usr/bin/env python3
"""The kiosk's launcher: wait for the monitoring page to answer, then become the browser, full-screen on it.

pom-display.service runs this under cage (a one-window Wayland compositor), so whatever this program starts is what is on the monitor. Everything it
needs is read from config/pom.toml: the page's address from `[monitor]`, the browser and its flags from `[display]`; the pause between asking is
`[monitor] poll_seconds`, the page's own beat.

It does not start the browser at all (and says why, in the journal) when the page could not be shown: the site is not enabled, has no password so it
will not run, or asks the Printer's own machine for a password (`[monitor] password_on_localhost`), which a screen with no keyboard cannot give.
It exits 0 then, so the service is not restarted in a loop for a setting that only a person can change.

Usage (the service runs it; a person can too, inside a graphical session):
    kiosk_browser.py                    # the config at config/pom.toml
    kiosk_browser.py --config FILE
"""
import argparse
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Optional, Sequence

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from pom.config import Config  # noqa: E402


def page_url(monitor) -> str:
    """The monitoring page's address as seen from the Printer itself."""
    return f"http://127.0.0.1:{monitor.port}/"


def refusal(config) -> Optional[str]:
    """Why the page cannot be shown on this machine's own screen, or None if it can."""
    monitor = config.monitor
    if not monitor.enabled:
        return "the monitoring site is not enabled ([monitor] enabled), so there is no page to show"
    if not monitor.password:
        return "the monitoring site has no password ([monitor] password in config/pom.local.toml), so it does not run and there is no page to show"
    if monitor.password_on_localhost:
        return ("the monitoring site asks the Printer's own machine for a password ([monitor] password_on_localhost = true), "
                "and a screen with no keyboard cannot give one; set it to false to show the page here")
    return None


def browser_command(display, url: str) -> list:
    """The browser's command line: the browser, its flags, its own profile folder, and the page's address."""
    return [display.browser, *display.browser_flags, f"--user-data-dir={os.path.expanduser(display.browser_profile_dir)}", url]


def wait_for_page(url: str, pause_seconds: float, opener: Callable = urllib.request.urlopen, sleep: Callable = time.sleep,
                  say: Callable = print) -> None:
    """Returns once the page answers, asking again every `pause_seconds` until it does (an ask that takes longer than that counts as no answer).
    Any answer counts, even a refusal: the Printer is up."""
    told = False
    while True:
        try:
            opener(url, timeout=pause_seconds).close()
            return
        except urllib.error.HTTPError:
            return
        except (urllib.error.URLError, OSError):
            if not told:
                say(f"kiosk: waiting for the monitoring page at {url}")
                told = True
            sleep(pause_seconds)


def main(argv: Optional[Sequence[str]] = None, execvp: Callable = os.execvp, opener: Callable = urllib.request.urlopen,
         sleep: Callable = time.sleep) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--config", default=str(ROOT / "config" / "pom.toml"), help="the config file (default config/pom.toml)")
    args = parser.parse_args(argv)

    config = Config.load(args.config)
    reason = refusal(config)
    if reason is not None:
        print(f"kiosk: not starting the browser: {reason}", flush=True)
        return 0
    url = page_url(config.monitor)
    wait_for_page(url, config.monitor.poll_seconds, opener, sleep)
    command = browser_command(config.display, url)
    print(f"kiosk: starting {command[0]} on {url}", flush=True)
    execvp(command[0], command)                          # becomes the browser; only returns (in a test) because execvp was replaced
    return 0


if __name__ == "__main__":
    sys.exit(main())
