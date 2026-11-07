"""Tests for scripts/setup/kiosk_browser.py: it waits for the monitoring page, then becomes the browser, full-screen on it."""
import importlib.util
import urllib.error
from pathlib import Path
from types import SimpleNamespace

import pytest

from pom.config import Config, DisplaySettings, MonitorSettings

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "setup" / "kiosk_browser.py"


@pytest.fixture(scope="module")
def kiosk():
    spec = importlib.util.spec_from_file_location("kiosk_browser", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def config(**monitor):
    values = dict(enabled=True, password="pw", password_on_localhost=False, port=8080, poll_seconds=5.0)
    values.update(monitor)
    return Config(monitor=MonitorSettings(**values))


def config_file(tmp_path, text):
    path = tmp_path / "pom.toml"
    path.write_text(text, encoding="utf-8")
    return str(path)


GOOD = '[monitor]\nenabled = true\npassword = "pw"\npassword_on_localhost = false\nport = 9123\npoll_seconds = 2.5\n'


class Answers:
    """A stand-in for urlopen that answers as told: each item is an exception to raise or None to answer."""

    def __init__(self, *outcomes):
        self.outcomes, self.calls = list(outcomes), []

    def __call__(self, url, timeout=None):
        self.calls.append((url, timeout))
        outcome = self.outcomes.pop(0) if self.outcomes else None
        if outcome is not None:
            raise outcome
        return SimpleNamespace(close=lambda: None)


class TestTheAddress:
    def test_it_is_the_printers_own_page_on_the_configured_port(self, kiosk):
        assert kiosk.page_url(MonitorSettings(port=9123)) == "http://127.0.0.1:9123/"

    def test_it_is_the_default_port_by_default(self, kiosk):
        assert kiosk.page_url(MonitorSettings()) == "http://127.0.0.1:8080/"


class TestWhenThePageCannotBeShown:
    def test_a_site_that_is_not_enabled(self, kiosk):
        assert "not enabled" in kiosk.refusal(config(enabled=False))

    def test_a_site_with_no_password_does_not_run(self, kiosk):
        assert "no password" in kiosk.refusal(config(password=""))

    def test_a_site_that_asks_the_printer_itself_for_a_password(self, kiosk):
        reason = kiosk.refusal(config(password_on_localhost=True))
        assert "password_on_localhost" in reason and "false" in reason

    def test_otherwise_it_can(self, kiosk):
        assert kiosk.refusal(config()) is None

    def test_the_reasons_are_checked_in_order(self, kiosk):
        assert "not enabled" in kiosk.refusal(config(enabled=False, password="", password_on_localhost=True))


class TestTheBrowserCommand:
    def display(self, **kw):
        return DisplaySettings(**{"browser": "chromium", "browser_flags": ("--kiosk", "--no-first-run"), "browser_profile_dir": "/tmp/profile", **kw})

    def test_it_is_the_browser_its_flags_its_profile_and_then_the_address(self, kiosk):
        assert kiosk.browser_command(self.display(), "http://127.0.0.1:8080/") == \
            ["chromium", "--kiosk", "--no-first-run", "--user-data-dir=/tmp/profile", "http://127.0.0.1:8080/"]

    def test_the_address_is_last_so_the_flags_cannot_swallow_it(self, kiosk):
        assert kiosk.browser_command(self.display(browser_flags=()), "http://x/")[-1] == "http://x/"

    def test_no_flags_is_allowed(self, kiosk):
        assert kiosk.browser_command(self.display(browser_flags=()), "http://x/") == ["chromium", "--user-data-dir=/tmp/profile", "http://x/"]

    def test_the_home_directory_is_filled_in(self, kiosk, monkeypatch):
        monkeypatch.setenv("HOME", "/home/pi")
        assert "--user-data-dir=/home/pi/.config/pom-kiosk" in kiosk.browser_command(DisplaySettings(), "http://x/")

    def test_a_full_path_to_the_browser_is_kept(self, kiosk):
        assert kiosk.browser_command(self.display(browser="/opt/browser/bin/b"), "http://x/")[0] == "/opt/browser/bin/b"

    def test_the_shipped_flags_are_kiosk_mode_on_wayland(self, kiosk):
        command = kiosk.browser_command(DisplaySettings(), "http://x/")
        assert "--kiosk" in command and "--ozone-platform=wayland" in command


class TestWaitingForThePage:
    def test_a_page_that_is_up_is_not_waited_for(self, kiosk):
        slept = []
        kiosk.wait_for_page("http://x/", 5.0, opener=Answers(None), sleep=slept.append, say=lambda *a: None)
        assert slept == []

    def test_it_asks_again_every_poll_beat_until_it_answers(self, kiosk):
        slept, answers = [], Answers(urllib.error.URLError("refused"), urllib.error.URLError("refused"), None)
        kiosk.wait_for_page("http://x/", 2.5, opener=answers, sleep=slept.append, say=lambda *a: None)
        assert slept == [2.5, 2.5] and len(answers.calls) == 3

    def test_an_ask_may_take_no_longer_than_the_beat(self, kiosk):
        answers = Answers(None)
        kiosk.wait_for_page("http://x/", 2.5, opener=answers, sleep=lambda s: None, say=lambda *a: None)
        assert answers.calls == [("http://x/", 2.5)]

    def test_a_refusal_is_an_answer_the_printer_is_up(self, kiosk):
        slept, answers = [], Answers(urllib.error.HTTPError("http://x/", 401, "no", {}, None))
        kiosk.wait_for_page("http://x/", 5.0, opener=answers, sleep=slept.append, say=lambda *a: None)
        assert slept == [] and len(answers.calls) == 1                       # it asked once and was done

    def test_a_socket_error_is_no_answer_yet(self, kiosk):
        slept = []
        kiosk.wait_for_page("http://x/", 1.0, opener=Answers(ConnectionRefusedError(), TimeoutError(), None), sleep=slept.append, say=lambda *a: None)
        assert slept == [1.0, 1.0]

    def test_it_says_so_once_not_every_time(self, kiosk):
        said = []
        kiosk.wait_for_page("http://x/", 1.0, opener=Answers(*[urllib.error.URLError("no")] * 5, None), sleep=lambda s: None, say=said.append)
        assert said == ["kiosk: waiting for the monitoring page at http://x/"]

    def test_it_says_nothing_when_there_is_nothing_to_wait_for(self, kiosk):
        said = []
        kiosk.wait_for_page("http://x/", 1.0, opener=Answers(None), sleep=lambda s: None, say=said.append)
        assert said == []

    def test_the_answer_is_closed(self, kiosk):
        closed = []
        kiosk.wait_for_page("http://x/", 1.0, opener=lambda url, timeout=None: SimpleNamespace(close=lambda: closed.append(1)), sleep=lambda s: None)
        assert closed == [1]


class TestStarting:
    def run(self, kiosk, tmp_path, text=GOOD, answers=None):
        calls, slept = [], []
        code = kiosk.main(["--config", config_file(tmp_path, text)], execvp=lambda file, argv: calls.append((file, argv)),
                          opener=answers or Answers(None), sleep=slept.append)
        return code, calls, slept

    def test_it_becomes_the_browser_on_the_page(self, kiosk, tmp_path):
        code, calls, _ = self.run(kiosk, tmp_path)
        assert code == 0 and len(calls) == 1
        file, argv = calls[0]
        assert file == "chromium" and argv[0] == "chromium" and argv[-1] == "http://127.0.0.1:9123/" and "--kiosk" in argv

    def test_the_port_and_the_beat_come_from_the_config(self, kiosk, tmp_path):
        answers = Answers(urllib.error.URLError("no"), None)
        _, _, slept = self.run(kiosk, tmp_path, answers=answers)
        assert slept == [2.5] and answers.calls[0] == ("http://127.0.0.1:9123/", 2.5)

    def test_the_display_settings_come_from_the_config(self, kiosk, tmp_path):
        text = GOOD + '[display]\nbrowser = "/opt/b"\nbrowser_flags = ["--one"]\nbrowser_profile_dir = "/tmp/p"\n'
        _, calls, _ = self.run(kiosk, tmp_path, text)
        assert calls[0] == ("/opt/b", ["/opt/b", "--one", "--user-data-dir=/tmp/p", "http://127.0.0.1:9123/"])

    @pytest.mark.parametrize("text, said", [
        ('[monitor]\nenabled = false\npassword = "pw"\npassword_on_localhost = false\n', "not enabled"),
        ("[monitor]\nenabled = true\npassword_on_localhost = false\n", "no password"),
        ('[monitor]\nenabled = true\npassword = "pw"\n', "password_on_localhost")])
    def test_when_the_page_cannot_be_shown_it_says_why_and_exits_cleanly_without_the_browser(self, kiosk, tmp_path, capsys, text, said):
        code, calls, slept = self.run(kiosk, tmp_path, text)
        assert code == 0 and calls == [] and slept == [] and said in capsys.readouterr().out

    def test_it_does_not_wait_when_it_will_not_start(self, kiosk, tmp_path):
        answers = Answers()
        self.run(kiosk, tmp_path, '[monitor]\nenabled = false\n', answers=answers)
        assert answers.calls == []

    def test_it_says_what_it_starts(self, kiosk, tmp_path, capsys):
        self.run(kiosk, tmp_path)
        assert "kiosk: starting chromium on http://127.0.0.1:9123/" in capsys.readouterr().out

    def test_it_runs_as_a_script(self, tmp_path):
        import subprocess
        import sys
        result = subprocess.run([sys.executable, str(SCRIPT), "--config", config_file(tmp_path, '[monitor]\nenabled = false\n')],
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == 0 and "not starting the browser" in result.stdout

    def test_the_committed_config_is_ready_for_the_kiosk(self, kiosk):
        shipped = Config.load(ROOT / "config" / "pom.toml", environ={})
        assert shipped.monitor.password_on_localhost is False and shipped.display.browser == "chromium"
