"""The MonitorSite: a web page of the last hour of turns, for a person to open from another machine.

Open it on the Printer's network (for example http://pom1.local:8080) and it shows how the Printer is doing and the newest
turns first. Click a turn for its details: the hinge, the exemplars the oracle was shown, the portent it wrote, the
score, the bar it had to beat and the window at that moment. The turns come from a TurnHistory; the Conductor adds to
it and this module only reads.

Design decisions, and why:
  * THE HINGE IS WHAT SOMEONE SAID IN THE ROOM. The page asks for a password (HTTP basic auth, any user name; the password
    is compared in constant time), the site will not start without one, and `show_hinges=False` hides the hinge text and
    shows numbers only. Nothing the page shows is stored anywhere but the TurnHistory's memory. The pages are never
    cached (`Cache-Control: no-store`), so a browser does not keep them.
  * All text is escaped by the template engine (Flask turns on autoescape for these templates), because the hinge and the
    portent are untrusted words. The only script is a few lines inline, and a Content-Security-Policy allows nothing else.
  * The page updates itself: a few lines of script ask `/api/turns` every few seconds, update the numbers in place, and
    reload the page when a newer turn has arrived. There are no websockets. The same feed is a plain JSON document.
  * THE CHART. With a ChartNotebook the page draws the last 30 minutes of scores and the bar above "Turns, newest first" (see
    score_chart), as inline SVG made on the server; the same script refreshes it from `/chart.svg` on each poll. It is numbers only,
    so it is drawn whether or not hinges are hidden, and its qualifier dots link to the turn pages.
  * Nothing here may stop the oracle (see pom/__init__.py). A missing password, a port that is taken, or a status function
    that fails is recorded in `report` (CRITICAL for the first two: the site is not running) and never raises.
    `strict=True` raises MonitorSiteError instead, for tests and dev tools.
  * The server is werkzeug's own, in a thread of its own, one request at a time per connection and several connections at
    once. It is the server Flask ships for development, meant for a single reader on a trusted network behind a password,
    which is what this is. Start and stop are the only places that know about it, so it can be swapped.
  * What the page says about the Printer's state (the bar now, how many attempts were heard, which servers are up) is not
    known here: the caller passes a function that returns a MonitorStatus. Without one the page says only what the history
    knows.
  * This module never reads Config; the entry point passes the settings in.
"""
import hmac
import ipaddress
import math
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Mapping, Optional

from flask import Flask, Response, abort, jsonify, render_template_string, request
from werkzeug.serving import WSGIRequestHandler, make_server

from pom.fault_recorder import FaultRecorder
from pom.log_setup import get_logger
from pom.monitor.score_chart import ChartLook, render_score_chart
from pom.monitor.chart_notebook import ChartNotebook
from pom.monitor.turn_history import TurnHistory, TurnRecord

DEBUG = None   # None follows the project debug flag; True/False overrides it for this module 
_log = get_logger(__name__, DEBUG)


class _PollingRequestHandler(WSGIRequestHandler):
    """werkzeug's request handler, except that the line it prints for every request goes through this module's logger, at DEBUG.

    The page asks `/api/turns` and `/chart.svg` every few seconds for as long as it is open. werkzeug prints a line for each straight
    to the console, whatever the logging settings say, which buries everything else. As a DEBUG line from this module's logger it
    follows the usual rules: hidden at INFO, shown when this module (or the whole project) is at DEBUG. The line holds the path and
    the status, no text of any turn. Problems werkzeug reports itself are not touched.
    """

    def log_request(self, code="-", size="-"):
        _log.debug('%s "%s" %s', self.address_string(), self.requestline, code)

REALM = "Printer of Mysteries"
DEFAULT_POLL_SECONDS = 5.0      # the fallback for a missing or bad `[monitor] poll_seconds`; the setting is the one that counts
SNIPPET_CHARS = 90
SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'",
}


class MonitorSiteError(ValueError):
    """Raised for any site problem, but only in strict mode (development and tests)."""


@dataclass(frozen=True, slots=True)
class MonitorStatus:
    """How the Printer is doing right now, as the caller knows it.

    Attributes:
        bar: The score a new score must be above to qualify now, or None until there is one.
        window_size: How many scores the window holds now.
        attempts_heard: How many attempts have been heard since the Printer started.
        attempts_scored: How many of them were scored.
        services: Whether each thing the Printer depends on is answering, by name, for example {"whisper": True}: True is up, False is down,
            None is none (there is no such thing to answer, for example no terminal is configured).
        algorithm: The name of the bar type in use, for example "decaying_quantile" ("" if not known).
        bar_description: One line saying how the BarKeeper works and how it is set (numbers only), shown beside the algorithm.
    """
    bar: Optional[float] = None
    window_size: int = 0
    attempts_heard: int = 0
    attempts_scored: int = 0
    services: Mapping[str, Optional[bool]] = field(default_factory=dict)
    algorithm: str = ""
    bar_description: str = ""


def _ago(seconds: float) -> str:
    """How long ago, in a few words: 'just now', '42 s ago', '7 min ago', '1 h 05 min ago'."""
    seconds = max(0, int(seconds))
    if seconds < 5:
        return "just now"
    if seconds < 60:
        return f"{seconds} s ago"
    if seconds < 3600:
        return f"{seconds // 60} min ago"
    return f"{seconds // 3600} h {seconds % 3600 // 60:02d} min ago"


def _clock_time(epoch: float) -> str:
    """The local time of day, hh:mm:ss."""
    return time.strftime("%H:%M:%S", time.localtime(epoch))


def _snippet(text: str) -> str:
    """The start of a hinge for the list, with an ellipsis if it was cut."""
    return text if len(text) <= SNIPPET_CHARS else text[:SNIPPET_CHARS].rstrip() + "…"


_STYLE = """
:root { --bg:#fbfaf7; --fg:#1d1b18; --muted:#6b665e; --line:#dcd6c9; --card:#fff; --good:#1f7a3a; --bad:#b3261e; --accent:#7a3b12; --dot:#000; --bar:#d62828; }
@media (prefers-color-scheme: dark) { :root { --bg:#16140f; --fg:#ece7dc; --muted:#a39d90; --line:#3a352b; --card:#1f1c15; --good:#5cc27a; --bad:#ff8a80; --accent:#e0a36b; --dot:#f2efe6; --bar:#ff6b6b; } }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--fg); font:16px/1.5 system-ui, sans-serif; }
main { max-width: 56rem; margin: 0 auto; padding: 1rem 16px 3rem; }
h1 { font-size: 1.3rem; margin: .5rem 0 1rem; } h2 { font-size: 1.05rem; margin: 1.6rem 0 .5rem; }
a { color: var(--accent); }
.tiles { display:grid; grid-template-columns: repeat(auto-fit, minmax(9rem, 1fr)); gap:.6rem; }
.tile { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:.6rem .8rem; }
.tile b { display:block; font-size:1.35rem; } .tile span { color:var(--muted); font-size:.85rem; }
.services span { margin-right:1rem; white-space:nowrap; } .up { color:var(--good); } .down { color:var(--bad); }
table { width:100%; border-collapse: collapse; background:var(--card); border:1px solid var(--line); border-radius:8px; }
th, td { text-align:left; padding:.5rem .6rem; border-bottom:1px solid var(--line); vertical-align: top; }
th { color:var(--muted); font-weight:600; font-size:.85rem; } td.num { text-align:right; font-variant-numeric: tabular-nums; }
pre { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:.7rem .9rem; white-space: pre-wrap; word-wrap: break-word; font: inherit; }
.chartbox { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:.4rem .5rem; margin:.6rem 0 .2rem; }
.chart { width:100%; height:auto; display:block; } .chart .grid, .chart .vgrid { stroke:var(--line); stroke-width:1; }
.chart .key { fill:var(--muted); font:10px system-ui, sans-serif; } .chart .dot { fill:var(--dot); } .chart .tick { fill:var(--line); } .chart .gap { fill:var(--card); } .chart .break { stroke:var(--muted); fill:none; stroke-width:1; }
.chart .bar { stroke:var(--bar); fill:none; } .chart .hit { fill:#e8872f; stroke:var(--bg); stroke-width:1; }
.small { font-size:.8rem; }
.muted { color: var(--muted); } .facts td:first-child { color:var(--muted); width:11rem; }
@media (max-width: 40rem) { .hide-narrow { display:none; } }
"""

_SCRIPT = """
(function () {
  var newest = %(newest)s;
  function set(name, value) { var el = document.querySelector('[data-stat="' + name + '"]'); if (el) el.textContent = value; }
  function drawChart() {
    var box = document.getElementById('chart');
    if (box) { fetch('/chart.svg', {cache: 'no-store'}).then(function (r) { return r.text(); }).then(function (svg) { box.innerHTML = svg; }); }
  }
  function poll() {
    drawChart();
    fetch('/api/turns', {cache: 'no-store'}).then(function (r) { return r.json(); }).then(function (data) {
      var s = data.stats;
      set('in_history', s.turns_in_history); set('per_hour', s.turns_per_hour); set('since_start', s.turns_since_start);
      set('last_ago', s.last_turn_ago); set('bar', s.bar_text); set('window', s.window_size); set('heard', s.attempts_heard);
      set('scored', s.attempts_scored); set('uptime', s.uptime);
      var latest = data.turns.length ? data.turns[0].id : null;
      if (latest !== newest) { location.reload(); }
      set('connection', '');
    }).catch(function () { set('connection', 'connection lost, retrying'); });
  }
  setInterval(poll, %(every)g * 1000);
})();
"""

_INDEX = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Printer monitor</title><style>{{ style }}</style></head>
<body><main>
<h1>Printer of Mysteries <span class="muted">· monitor</span> <span class="down" data-stat="connection"></span></h1>
<div class="tiles">
  <div class="tile"><b data-stat="in_history">{{ s.turns_in_history }}</b><span>turns in the last {{ s.history_minutes }} min</span></div>
  <div class="tile"><b data-stat="per_hour">{{ s.turns_per_hour }}</b><span>an hour at that pace</span></div>
  <div class="tile"><b data-stat="last_ago">{{ s.last_turn_ago }}</b><span>last turn</span></div>
  <div class="tile"><b data-stat="bar">{{ s.bar_text }}</b><span>bar to beat now</span></div>
  <div class="tile"><b data-stat="window">{{ s.window_size }}</b><span>scores in the window</span></div>
  <div class="tile"><b><span data-stat="scored" style="font-size:inherit;color:inherit">{{ s.attempts_scored }}</span> / <span data-stat="heard" style="font-size:inherit;color:inherit">{{ s.attempts_heard }}</span></b><span>attempts scored / heard</span></div>
  <div class="tile"><b data-stat="since_start">{{ s.turns_since_start }}</b><span>turns since start</span></div>
  <div class="tile"><b data-stat="uptime">{{ s.uptime }}</b><span>up</span></div>
</div>
{% if services %}<p class="services">{% for name, up in services %}<span>{{ name }}: {% if up is none %}<b class="muted">none</b>{% else %}<b class="{{ 'up' if up else 'down' }}">{{ 'up' if up else 'DOWN' }}</b>{% endif %}</span>{% endfor %}</p>{% endif %}
{% if s.algorithm or s.bar_description %}<p class="rule"><span class="muted">Bar type:</span> <b>{{ s.algorithm }}</b>{% if s.bar_description %} <span class="muted">&mdash; a score above {{ s.bar_description }}</span>{% endif %}</p>{% endif %}
{% if chart %}<div id="chart" class="chartbox">{{ chart|safe }}</div>
<p class="muted small">Black dots: attempts. Red line: the bar each one had to beat. Orange: qualifiers (click one). The last {{ chart_minutes }} minutes.</p>{% endif %}
<h2>Turns, newest first</h2>
{% if rows %}
<table><thead><tr><th>Time</th><th class="num">Score</th><th class="num">Bar</th><th class="hide-narrow num">Window</th><th>Hinge</th></tr></thead><tbody>
{% for r in rows %}<tr>
  <td><a href="/turn/{{ r.id }}">{{ r.time }}</a><br><span class="muted">{{ r.ago }}</span></td>
  <td class="num">{{ r.score }}</td><td class="num">{{ r.bar }}</td><td class="hide-narrow num">{{ r.window_size }}</td>
  <td>{{ r.hinge }}</td></tr>{% endfor %}
</tbody></table>
{% else %}<p class="muted">No turns in the last {{ s.history_minutes }} minutes.</p>{% endif %}
<script>{{ script|safe }}</script>
</main></body></html>"""

_DETAIL = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Turn {{ r.id }}</title><style>{{ style }}</style></head>
<body><main>
<p><a href="/">&larr; all turns</a></p>
<p class="nav">{% if newer %}<a href="/turn/{{ newer }}" rel="prev">&larr; Newer</a>{% else %}<span class="muted">&larr; Newer</span>{% endif %}
 &nbsp;·&nbsp; {% if older %}<a href="/turn/{{ older }}" rel="next">Older &rarr;</a>{% else %}<span class="muted">Older &rarr;</span>{% endif %}</p>
<h1>Turn {{ r.id }} <span class="muted">· {{ time }} ({{ ago }})</span></h1>
<h2>Hinge</h2>
<pre>{{ hinge }}</pre>
<table class="facts"><tbody>
<tr><td>Score</td><td>{{ score }}</td></tr>
<tr><td>Bar it had to beat</td><td>{{ bar }}</td></tr>
<tr><td>Scores in the window</td><td>{{ r.window_size }}</td></tr>
<tr><td>Heard at</td><td>{{ heard_at }} into the listening</td></tr>
{% if rule %}<tr><td>Bar type</td><td>{{ rule }}</td></tr>{% endif %}
</tbody></table>
<h2>Portent</h2>
{% if portent %}
<pre>{{ portent.text }}</pre>
<p class="muted">{{ portent.words }} words, written in {{ seconds }} s{% if portent.cut_off %}, cut off at the most it may write{% endif %}.</p>
<h2>Exemplars the oracle was shown</h2>
{% for passage in portent.exemplars %}<p class="muted">{{ passage.id }} · {{ passage.shelf }}</p><pre>{{ passage.text }}</pre>
{% else %}<p class="muted">None.</p>{% endfor %}
{% else %}<p class="muted">No portent could be had for this turn.</p>{% endif %}
</main></body></html>"""


class MonitorSite:
    """The monitoring site.

    Attributes:
        report: Problems met (no password, the port taken, the status function failing).
        app: The Flask application, or None if there is no password (the site cannot run).
    """

    def __init__(self, history: TurnHistory, password: str, show_hinges: bool = True,
                 status_source: Optional[Callable[[], MonitorStatus]] = None,
                 clock: Callable[[], float] = time.time, strict: bool = False, notebook: Optional[ChartNotebook] = None, chart=None,
                 poll_seconds: float = DEFAULT_POLL_SECONDS, stream_now: Optional[Callable[[], float]] = None,
                 ticks_source: Optional[Callable[[float, float], list]] = None, stream_began_at: Optional[float] = None,
                 password_on_localhost: bool = True):
        """Args:
            history: Where the turns are read from.
            password: The site's password; the site does not run without one.
            show_hinges: False hides the hinge text everywhere, leaving numbers only.
            status_source: A function returning the MonitorStatus now; optional.
            clock: Returns seconds since the epoch; injected so tests need not wait.
            strict: Raise MonitorSiteError on any problem instead of recording it.
            notebook: A ChartNotebook to draw the chart from; without one the page has no chart.
            chart: The `[chart]` settings that shape the chart (anything with the fields of `ChartLook`); None means the fallbacks.
            poll_seconds: How often the open page asks for news and redraws (`[monitor] poll_seconds`), above 0; a bad value is the fallback, with a warning.
            stream_now: Returns the stream time now, the chart's right edge, so the chart moves through silence; without it the edge is the newest attempt.
            password_on_localhost: False lets a request from the Printer's own machine in without the password (see `_from_this_machine`); True, the
                default and the fallback for anything that is not a bool, asks everyone.
            stream_began_at: The stream time at which listening began; a quiet start (no attempt for longer than the longest silence) is drawn as a
                silence too, as long as the notebook still holds every attempt since then.
            ticks_source: Called with (start, end) in stream time; returns the anchor's ticks between them, each as (time, anchor score, bar then). The page asks it only for the live stretch since the newest attempt;
                the ticks before that are in the notebook.
        """
        self.report = FaultRecorder(strict=strict, error=MonitorSiteError, logger=_log)
        self._history = history
        self._show_hinges = bool(show_hinges)
        self._status_source = status_source
        self._notebook = notebook
        self._look = chart if chart is not None else ChartLook()
        self._clock = clock
        self._stream_now = stream_now
        self._ticks_source = ticks_source
        self._stream_began_at = stream_began_at
        if not isinstance(password_on_localhost, bool):
            self.report.warn(f"password_on_localhost must be true or false, not {password_on_localhost!r}; asking everyone for the password")
            password_on_localhost = True
        self._password_on_localhost = password_on_localhost
        self._poll_seconds = poll_seconds
        if not (isinstance(poll_seconds, (int, float)) and not isinstance(poll_seconds, bool) and math.isfinite(poll_seconds) and poll_seconds > 0):
            self.report.warn(f"poll_seconds must be a number above 0, not {poll_seconds!r}; using {DEFAULT_POLL_SECONDS:g}")
            self._poll_seconds = DEFAULT_POLL_SECONDS
        self._started_at = clock()
        self._server = None
        self._thread: Optional[threading.Thread] = None
        self.app: Optional[Flask] = None
        if not (isinstance(password, str) and password):
            self.report.critical("the monitoring site needs a password and none was set (put one under [monitor] in "
                                 "config/pom.local.toml), so it will not start")
            return
        self._password = password.encode("utf-8")
        self.app = self._build_app()

    # --- Starting and stopping ------------------------------------------------------

    def start(self, host: str, port: int) -> bool:
        """Starts serving in a thread of its own. True if it is running; False (see `report`) if not."""
        if self.app is None or self._server is not None:
            return self._server is not None
        try:
            self._server = make_server(host, port, self.app, threaded=True, request_handler=_PollingRequestHandler)
        except (OSError, OverflowError, ValueError, SystemExit) as problem:      # werkzeug exits the process if the port is taken
            self.report.critical(f"the monitoring site could not listen on port {port} ({type(problem).__name__}); "
                                 f"it is not running")
            return False
        self._thread = threading.Thread(target=self._server.serve_forever, name="monitor-site", daemon=True)
        self._thread.start()
        _log.info("monitoring site listening on port %d", self._server.server_port)
        return True

    @property
    def port(self) -> Optional[int]:
        """The port it is listening on (useful when asked for port 0), or None if it is not running."""
        return self._server.server_port if self._server is not None else None

    def stop(self) -> None:
        """Stops serving. Safe to call more than once."""
        server, self._server = self._server, None
        if server is None:
            return
        server.shutdown()
        server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)

    # --- What the pages say ---------------------------------------------------------

    def _status(self) -> MonitorStatus:
        """The caller's status, or an empty one if there is no holding or it fails."""
        if self._status_source is None:
            return MonitorStatus()
        try:
            status = self._status_source()
            if isinstance(status, MonitorStatus):
                return status
            self.report.warn("the status function did not return a MonitorStatus; the page shows no state")
        except Exception as problem:               # a failing status function must never take the page down
            self.report.warn(f"the status function failed ({type(problem).__name__}); the page shows no state")
        return MonitorStatus()

    def _stats(self, records) -> dict:
        """The numbers at the top of the page, as text where they are shown as text."""
        now = self._clock()
        status = self._status()
        span = self._history.max_age_seconds
        uptime = _ago(now - self._started_at).replace(" ago", "")
        return {
            "turns_in_history": len(records),
            "turns_per_hour": f"{len(records) * 3600 / span:.1f}",
            "history_minutes": f"{span / 60:g}",
            "turns_since_start": self._history.total_added,
            "last_turn_ago": _ago(now - records[0].at) if records else "none yet",
            "bar": status.bar,
            "bar_text": f"{status.bar:.2f}" if isinstance(status.bar, (int, float)) else "none yet",
            "window_size": status.window_size,
            "attempts_heard": status.attempts_heard,
            "attempts_scored": status.attempts_scored,
            "uptime": "just started" if uptime == "just now" else uptime,
            "services": {str(name): (None if up is None else bool(up)) for name, up in dict(status.services).items()},
            "algorithm": str(status.algorithm or ""),
            "bar_description": str(status.bar_description or ""),
        }

    def _hinge_text(self, record: TurnRecord, short: bool) -> str:
        if not self._show_hinges:
            return "(hidden)"
        return _snippet(record.hinge) if short else record.hinge

    @staticmethod
    def _bar_text(record: TurnRecord) -> str:
        return f"{record.bar:.2f}" if record.bar is not None else "none yet"

    def _neighbours(self, record_id: int):
        """The numbers of the next newer and the next older turn still on the page (None where there is none)."""
        ids = [record.id for record in self._history.recent()]            # newest first
        if record_id not in ids:
            return None, None
        at = ids.index(record_id)
        return (ids[at - 1] if at > 0 else None), (ids[at + 1] if at + 1 < len(ids) else None)

    def _chart(self) -> str:
        """The chart as SVG, or "" if there is no notebook. Qualifier dots link to the records still on the page."""
        if self._notebook is None:
            return ""
        links = {record.heard_at: record.id for record in self._history.recent()}
        if self._stream_now is None:
            return render_score_chart(self._notebook.recent(self._look.window_minutes * 60.0), links, self._look)
        now = self._stream_now()
        points = self._notebook.recent()
        # the ticks across the whole chart: those the notebook wrote down (with the bar at each, worked out while the bar still held the scores), then the
        # live stretch since the newest attempt, which only now can be asked
        ticks = [(tick.at, tick.score, tick.bar) for tick in self._notebook.ticks()] + self._live_ticks(points, now)
        began_at = self._stream_began_at if self._notebook.holds_everything else None                  # else the start is no longer in what is drawn
        return render_score_chart(points, links, self._look, ticks=ticks, now=now, bar_now=self._status().bar, began_at=began_at)

    def _live_ticks(self, points, now: float) -> list:
        """The ticks since the newest attempt, up to now: the ones the notebook has not written down yet. Once that stretch is longer than the longest
        silence the chart draws it as a break and hides what is inside, so none are asked for. With no attempt at all, the chart's own width is asked for."""
        if self._ticks_source is None:
            return []
        if points:
            start = points[-1].at
            if now - start > self._look.longest_silence_minutes * 60.0:
                return []
        else:
            start = now - self._look.window_minutes * 60.0
        return [tick for tick in self._ticks_source(start, now) if tick[0] > start]                  # the start's own tick is the attempt's, already written

    def _row(self, record: TurnRecord, now: float) -> dict:
        return {"id": record.id, "time": _clock_time(record.at), "ago": _ago(now - record.at),
                "score": f"{record.score:.2f}", "bar": self._bar_text(record), "window_size": record.window_size,
                "hinge": self._hinge_text(record, short=True)}

    # --- The application ------------------------------------------------------------

    @staticmethod
    def _from_this_machine() -> bool:
        """True if the request came from the Printer's own machine: the connection's own address is loopback (127.0.0.1, ::1, or an IPv4 address
        in IPv6 dress) and nothing says it was passed on by a proxy (`X-Forwarded-For`, `Forwarded`, `X-Real-IP`). A proxy running on the Printer
        would make every request look local, so a request that names one is treated as coming from elsewhere. The address is the connection's, which a
        client cannot choose; a header is only ever used to say no."""
        if any(name in request.headers for name in ("X-Forwarded-For", "Forwarded", "X-Real-IP")):
            return False
        try:
            address = ipaddress.ip_address(request.remote_addr or "")
        except ValueError:
            return False
        if address.version == 6 and address.ipv4_mapped is not None:
            address = address.ipv4_mapped
        return address.is_loopback

    def _authorized(self) -> bool:
        """True if the request carries the right password (any user name), compared in constant time, or comes from this machine and
        `password_on_localhost` is off."""
        if not self._password_on_localhost and self._from_this_machine():
            return True
        auth = request.authorization
        given = (auth.password or "").encode("utf-8") if auth is not None and auth.type == "basic" else b""
        return hmac.compare_digest(given, self._password)

    def _build_app(self) -> Flask:
        app = Flask(__name__)

        @app.before_request
        def require_password():
            if not self._authorized():
                return Response("A password is needed.", 401, {"WWW-Authenticate": f'Basic realm="{REALM}", charset="UTF-8"'})

        @app.after_request
        def add_security_headers(response):
            for name, value in SECURITY_HEADERS.items():
                response.headers.setdefault(name, value)
            return response

        @app.get("/")
        def index():
            records = self._history.recent()
            stats = self._stats(records)
            now = self._clock()
            script = _SCRIPT % {"newest": records[0].id if records else "null", "every": self._poll_seconds}
            return render_template_string(_INDEX, style=_STYLE, script=script, s=stats, chart=self._chart(), chart_minutes=f"{self._look.window_minutes:g}",
                                          services=list(stats["services"].items()),
                                          rows=[self._row(record, now) for record in records])

        @app.get("/chart.svg")
        def chart_svg():
            if self._notebook is None:
                abort(404)
            return Response(self._chart(), mimetype="image/svg+xml")

        @app.get("/turn/<int:record_id>")
        def detail(record_id: int):
            record = self._history.get(record_id)
            if record is None:
                abort(404)
            portent = record.portent
            newer, older = self._neighbours(record_id)
            status = self._status()
            rule = " - ".join(part for part in (str(status.algorithm or ""), str(status.bar_description or "")) if part)
            return render_template_string(
                _DETAIL, style=_STYLE, r=record, newer=newer, older=older, rule=rule, time=_clock_time(record.at), ago=_ago(self._clock() - record.at),
                hinge=self._hinge_text(record, short=False), score=f"{record.score:.2f}", bar=self._bar_text(record),
                heard_at=f"{int(record.heard_at) // 60} min {int(record.heard_at) % 60:02d} s", portent=portent,
                seconds=f"{portent.seconds:.1f}" if portent is not None else "")

        @app.get("/api/turns")
        def api_turns():
            records = self._history.recent()
            stats = self._stats(records)
            now = self._clock()
            turns = []
            for record in records:
                item = {"id": record.id, "at": record.at, "ago": _ago(now - record.at), "heard_at": record.heard_at,
                        "score": record.score, "bar": record.bar, "window_size": record.window_size,
                        "has_portent": record.portent is not None}
                if self._show_hinges:
                    item["hinge"] = record.hinge
                if record.portent is not None:
                    item.update(portent=record.portent.text, words=record.portent.words, seconds=record.portent.seconds,
                                cut_off=record.portent.cut_off, exemplars=[passage.id for passage in record.portent.exemplars])
                turns.append(item)
            return jsonify({"stats": stats, "turns": turns})

        return app
