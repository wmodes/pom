"""The MonitorSite: a web page of the last hour of triggers, for a person to open from another machine.

Open it on the Printer's network (for example http://pom1.local:8080) and it shows how the Printer is doing and the newest
triggers first. Click a trigger for its details: the hinge, the exemplars the oracle was shown, the portent it wrote, the
score, the bar it had to beat and the window at that moment. The triggers come from a TriggerHistory; the Conductor adds to
it and this module only reads.

Design decisions, and why:
  * THE HINGE IS WHAT SOMEONE SAID IN THE ROOM. The page asks for a password (HTTP basic auth, any user name; the password
    is compared in constant time), the site will not start without one, and `show_hinges=False` hides the hinge text and
    shows numbers only. Nothing the page shows is stored anywhere but the TriggerHistory's memory. The pages are never
    cached (`Cache-Control: no-store`), so a browser does not keep them.
  * All text is escaped by the template engine (Flask turns on autoescape for these templates), because the hinge and the
    portent are untrusted words. The only script is a few lines inline, and a Content-Security-Policy allows nothing else.
  * The page updates itself: a few lines of script ask `/api/triggers` every few seconds, update the numbers in place, and
    reload the page when a newer trigger has arrived. There are no websockets. The same feed is a plain JSON document.
  * Nothing here may stop the oracle (see pom/__init__.py). A missing password, a port that is taken, or a status function
    that fails is recorded in `report` (CRITICAL for the first two: the site is not running) and never raises.
    `strict=True` raises MonitorSiteError instead, for tests and dev tools.
  * The server is werkzeug's own, in a thread of its own, one request at a time per connection and several connections at
    once. It is the server Flask ships for development, meant for a single reader on a trusted network behind a password,
    which is what this is. Start and stop are the only places that know about it, so it can be swapped.
  * What the page says about the Printer's state (the bar now, how many sentences were heard, which servers are up) is not
    known here: the caller passes a function that returns a MonitorStatus. Without one the page says only what the history
    knows.
  * This module never reads Config; the entry point passes the settings in.
"""
import hmac
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Mapping, Optional

from flask import Flask, Response, abort, jsonify, render_template_string, request
from werkzeug.serving import make_server

from pom.fault_recorder import FaultRecorder
from pom.log_setup import get_logger
from pom.monitor.trigger_history import TriggerHistory, TriggerRecord

DEBUG = None   # None follows the project debug flag; True/False overrides it for this module
_log = get_logger(__name__, DEBUG)

REALM = "Printer of Mysteries"
POLL_SECONDS = 5
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
        bar: The score a new score must be above to trigger now, or None until there is one.
        window_size: How many scores the window holds now.
        sentences_heard: How many sentences have been heard since the Printer started.
        sentences_scored: How many of them were scored.
        services: Whether each thing the Printer depends on is answering, by name, for example {"whisper": True}.
    """
    bar: Optional[float] = None
    window_size: int = 0
    sentences_heard: int = 0
    sentences_scored: int = 0
    services: Mapping[str, bool] = field(default_factory=dict)


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
:root { --bg:#fbfaf7; --fg:#1d1b18; --muted:#6b665e; --line:#dcd6c9; --card:#fff; --good:#1f7a3a; --bad:#b3261e; --accent:#7a3b12; }
@media (prefers-color-scheme: dark) { :root { --bg:#16140f; --fg:#ece7dc; --muted:#a39d90; --line:#3a352b; --card:#1f1c15; --good:#5cc27a; --bad:#ff8a80; --accent:#e0a36b; } }
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
.muted { color: var(--muted); } .facts td:first-child { color:var(--muted); width:11rem; }
@media (max-width: 40rem) { .hide-narrow { display:none; } }
"""

_SCRIPT = """
(function () {
  var newest = %(newest)s;
  function set(name, value) { var el = document.querySelector('[data-stat="' + name + '"]'); if (el) el.textContent = value; }
  function poll() {
    fetch('/api/triggers', {cache: 'no-store'}).then(function (r) { return r.json(); }).then(function (data) {
      var s = data.stats;
      set('in_history', s.triggers_in_history); set('per_hour', s.triggers_per_hour); set('since_start', s.triggers_since_start);
      set('last_ago', s.last_trigger_ago); set('bar', s.bar_text); set('window', s.window_size); set('heard', s.sentences_heard);
      set('scored', s.sentences_scored); set('uptime', s.uptime);
      var latest = data.triggers.length ? data.triggers[0].id : null;
      if (latest !== newest) { location.reload(); }
      set('connection', '');
    }).catch(function () { set('connection', 'connection lost, retrying'); });
  }
  setInterval(poll, %(every)d * 1000);
})();
"""

_INDEX = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Printer monitor</title><style>{{ style }}</style></head>
<body><main>
<h1>Printer of Mysteries <span class="muted">· monitor</span> <span class="down" data-stat="connection"></span></h1>
<div class="tiles">
  <div class="tile"><b data-stat="in_history">{{ s.triggers_in_history }}</b><span>triggers in the last {{ s.history_minutes }} min</span></div>
  <div class="tile"><b data-stat="per_hour">{{ s.triggers_per_hour }}</b><span>an hour at that pace</span></div>
  <div class="tile"><b data-stat="last_ago">{{ s.last_trigger_ago }}</b><span>last trigger</span></div>
  <div class="tile"><b data-stat="bar">{{ s.bar_text }}</b><span>bar to beat now</span></div>
  <div class="tile"><b data-stat="window">{{ s.window_size }}</b><span>scores in the window</span></div>
  <div class="tile"><b><span data-stat="scored" style="font-size:inherit;color:inherit">{{ s.sentences_scored }}</span> / <span data-stat="heard" style="font-size:inherit;color:inherit">{{ s.sentences_heard }}</span></b><span>sentences scored / heard</span></div>
  <div class="tile"><b data-stat="since_start">{{ s.triggers_since_start }}</b><span>triggers since start</span></div>
  <div class="tile"><b data-stat="uptime">{{ s.uptime }}</b><span>up</span></div>
</div>
{% if services %}<p class="services">{% for name, up in services %}<span>{{ name }}: <b class="{{ 'up' if up else 'down' }}">{{ 'up' if up else 'DOWN' }}</b></span>{% endfor %}</p>{% endif %}
<h2>Triggers, newest first</h2>
{% if rows %}
<table><thead><tr><th>Time</th><th class="num">Score</th><th class="num">Bar</th><th class="hide-narrow num">Window</th><th>Hinge</th></tr></thead><tbody>
{% for r in rows %}<tr>
  <td><a href="/trigger/{{ r.id }}">{{ r.time }}</a><br><span class="muted">{{ r.ago }}</span></td>
  <td class="num">{{ r.score }}</td><td class="num">{{ r.bar }}</td><td class="hide-narrow num">{{ r.window_size }}</td>
  <td>{{ r.hinge }}</td></tr>{% endfor %}
</tbody></table>
{% else %}<p class="muted">No triggers in the last {{ s.history_minutes }} minutes.</p>{% endif %}
<script>{{ script|safe }}</script>
</main></body></html>"""

_DETAIL = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Trigger {{ r.id }}</title><style>{{ style }}</style></head>
<body><main>
<p><a href="/">&larr; all triggers</a></p>
<h1>Trigger {{ r.id }} <span class="muted">· {{ time }} ({{ ago }})</span></h1>
<h2>Hinge</h2>
<pre>{{ hinge }}</pre>
<table class="facts"><tbody>
<tr><td>Score</td><td>{{ score }}</td></tr>
<tr><td>Bar it had to beat</td><td>{{ bar }}</td></tr>
<tr><td>Scores in the window</td><td>{{ r.window_size }}</td></tr>
<tr><td>Heard at</td><td>{{ heard_at }} into the listening</td></tr>
</tbody></table>
<h2>Portent</h2>
{% if portent %}
<pre>{{ portent.text }}</pre>
<p class="muted">{{ portent.words }} words, written in {{ seconds }} s{% if portent.cut_off %}, cut off at the most it may write{% endif %}.</p>
<h2>Exemplars the oracle was shown</h2>
{% for chunk in portent.exemplars %}<p class="muted">{{ chunk.id }} · {{ chunk.register }}</p><pre>{{ chunk.text }}</pre>
{% else %}<p class="muted">None.</p>{% endfor %}
{% else %}<p class="muted">No portent could be had for this trigger.</p>{% endif %}
</main></body></html>"""


class MonitorSite:
    """The monitoring site.

    Attributes:
        report: Problems met (no password, the port taken, the status function failing).
        app: The Flask application, or None if there is no password (the site cannot run).
    """

    def __init__(self, history: TriggerHistory, password: str, show_hinges: bool = True,
                 status_source: Optional[Callable[[], MonitorStatus]] = None,
                 clock: Callable[[], float] = time.time, strict: bool = False):
        """Args:
            history: Where the triggers are read from.
            password: The site's password; the site does not run without one.
            show_hinges: False hides the hinge text everywhere, leaving numbers only.
            status_source: A function returning the MonitorStatus now; optional.
            clock: Returns seconds since the epoch; injected so tests need not wait.
            strict: Raise MonitorSiteError on any problem instead of recording it.
        """
        self.report = FaultRecorder(strict=strict, error=MonitorSiteError, logger=_log)
        self._history = history
        self._show_hinges = bool(show_hinges)
        self._status_source = status_source
        self._clock = clock
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
            self._server = make_server(host, port, self.app, threaded=True)
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
        """The caller's status, or an empty one if there is no source or it fails."""
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
            "triggers_in_history": len(records),
            "triggers_per_hour": f"{len(records) * 3600 / span:.1f}",
            "history_minutes": f"{span / 60:g}",
            "triggers_since_start": self._history.total_added,
            "last_trigger_ago": _ago(now - records[0].at) if records else "none yet",
            "bar": status.bar,
            "bar_text": f"{status.bar:.2f}" if isinstance(status.bar, (int, float)) else "none yet",
            "window_size": status.window_size,
            "sentences_heard": status.sentences_heard,
            "sentences_scored": status.sentences_scored,
            "uptime": "just started" if uptime == "just now" else uptime,
            "services": {str(name): bool(up) for name, up in dict(status.services).items()},
        }

    def _hinge_text(self, record: TriggerRecord, short: bool) -> str:
        if not self._show_hinges:
            return "(hidden)"
        return _snippet(record.hinge) if short else record.hinge

    @staticmethod
    def _bar_text(record: TriggerRecord) -> str:
        return f"{record.bar:.2f}" if record.bar is not None else "none yet"

    def _row(self, record: TriggerRecord, now: float) -> dict:
        return {"id": record.id, "time": _clock_time(record.at), "ago": _ago(now - record.at),
                "score": f"{record.score:.2f}", "bar": self._bar_text(record), "window_size": record.window_size,
                "hinge": self._hinge_text(record, short=True)}

    # --- The application ------------------------------------------------------------

    def _authorized(self) -> bool:
        """True if the request carries the right password (any user name), compared in constant time."""
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
            script = _SCRIPT % {"newest": records[0].id if records else "null", "every": POLL_SECONDS}
            return render_template_string(_INDEX, style=_STYLE, script=script, s=stats,
                                          services=list(stats["services"].items()),
                                          rows=[self._row(record, now) for record in records])

        @app.get("/trigger/<int:record_id>")
        def detail(record_id: int):
            record = self._history.get(record_id)
            if record is None:
                abort(404)
            portent = record.portent
            return render_template_string(
                _DETAIL, style=_STYLE, r=record, time=_clock_time(record.at), ago=_ago(self._clock() - record.at),
                hinge=self._hinge_text(record, short=False), score=f"{record.score:.2f}", bar=self._bar_text(record),
                heard_at=f"{int(record.heard_at) // 60} min {int(record.heard_at) % 60:02d} s", portent=portent,
                seconds=f"{portent.seconds:.1f}" if portent is not None else "")

        @app.get("/api/triggers")
        def api_triggers():
            records = self._history.recent()
            stats = self._stats(records)
            now = self._clock()
            triggers = []
            for record in records:
                item = {"id": record.id, "at": record.at, "ago": _ago(now - record.at), "heard_at": record.heard_at,
                        "score": record.score, "bar": record.bar, "window_size": record.window_size,
                        "has_portent": record.portent is not None}
                if self._show_hinges:
                    item["hinge"] = record.hinge
                if record.portent is not None:
                    item.update(portent=record.portent.text, words=record.portent.words, seconds=record.portent.seconds,
                                cut_off=record.portent.cut_off, exemplars=[chunk.id for chunk in record.portent.exemplars])
                triggers.append(item)
            return jsonify({"stats": stats, "triggers": triggers})

        return app
