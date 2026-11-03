"""Tests for the systemd units and the scripts that link them (scripts/setup/).

The unit files are the real service files: `systemctl link` points systemd at them in the repo, so they are tested as files here, on the Mac,
against what they have to agree with: the ports in config/pom.toml, the flags the bench script starts the same servers with, and the
design (all the models loaded, the Printer wanting the servers and never requiring them)."""
import importlib.util
import os
import re
import shlex
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import pytest

from pom.config import Config

ROOT = Path(__file__).resolve().parents[2]
SETUP = ROOT / "scripts" / "setup"
UNITS = ["pom-whisper", "pom-scorer", "pom-oracle", "pom"]
SERVERS = ["pom-whisper", "pom-scorer", "pom-oracle"]
BOOLEAN_FLAGS = {"--jinja", "--vad"}


def read_unit(name):
    """A unit file as {section: {key: [values]}}; a key may repeat (Environment=), so every value is kept."""
    sections, current = {}, None
    for raw in (SETUP / "systemd" / f"{name}.service").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            current = sections.setdefault(line.strip("[]"), {})
        else:
            key, _, value = line.partition("=")
            current.setdefault(key, []).append(value)
    return sections


def exec_tokens(name):
    return shlex.split(read_unit(name)["Service"]["ExecStart"][0])


def flags(tokens):
    """{flag: value} for the flags in a command line; a boolean flag has the value True."""
    found, i = {}, 1
    while i < len(tokens):
        if tokens[i].startswith("-"):
            if tokens[i] in BOOLEAN_FLAGS or i + 1 >= len(tokens) or tokens[i + 1].startswith("-"):
                found[tokens[i]] = True
            else:
                found[tokens[i]] = tokens[i + 1]
                i += 1
        i += 1
    return found


def port_of(url):
    return str(urlparse(url).port)


@pytest.fixture(scope="module")
def config():
    return Config.load(ROOT / "config" / "pom.toml", environ={})


@pytest.mark.parametrize("name", UNITS)
class TestEveryUnit:
    def test_it_runs_as_pi_from_the_checkout_and_restarts_itself(self, name):
        service = read_unit(name)["Service"]
        assert service["User"] == ["pi"] and service["WorkingDirectory"] == ["/home/pi/pom"]
        assert service["Restart"] == ["always"] and int(service["RestartSec"][0]) >= 1

    def test_it_starts_at_boot(self, name):
        assert read_unit(name)["Install"]["WantedBy"] == ["multi-user.target"]

    def test_it_has_a_description_naming_the_project(self, name):
        assert "Printer of Mysteries" in read_unit(name)["Unit"]["Description"][0]

    def test_the_file_is_in_the_repo_where_the_install_script_links_it_from(self, name):
        assert (SETUP / "systemd" / f"{name}.service").is_file()
        assert f"{name}" in (SETUP / "install-services.sh").read_text(encoding="utf-8")


class TestTheServers:
    def test_the_ports_are_the_ones_in_the_config(self, config):
        assert flags(exec_tokens("pom-whisper"))["--port"] == port_of(config.listening.whisper_url)
        assert flags(exec_tokens("pom-scorer"))["--port"] == port_of(config.scorer.url)
        assert flags(exec_tokens("pom-oracle"))["--port"] == port_of(config.oracle.url)

    @pytest.mark.parametrize("name", SERVERS)
    def test_each_listens_on_this_machine_only(self, name):
        assert flags(exec_tokens(name))["--host"] == "127.0.0.1"

    @pytest.mark.parametrize("name,model", [("pom-scorer", "Qwen3-4B-Q4_K_M.gguf"), ("pom-oracle", "Qwen3.5-9B-Q4_K_M.gguf"),
                                            ("pom-whisper", "ggml-base.en.bin")])
    def test_each_serves_the_model_the_design_names(self, name, model):
        path = flags(exec_tokens(name))["-m"]
        assert path.endswith("/" + model) and path.startswith("/home/pi/pom/data/")

    @pytest.mark.parametrize("name", ["pom-scorer", "pom-oracle"])
    def test_the_language_models_stay_loaded_and_are_not_pinned(self, name):
        # Measured on the Pi: -lm mmap+mlock used the memory twice (the ARM build keeps a repacked copy of the weights) and left under 1 GB
        # available with both models loaded; plain mmap leaves about 7 GB and the models stay resident in the page cache.
        found = flags(exec_tokens(name))
        assert found["-lm"] == "mmap" and found["-lzm"] == "off"
        assert "--mlock" not in found and "LimitMEMLOCK" not in read_unit(name)["Service"]

    def test_whisper_uses_its_own_voice_detection(self):
        found = flags(exec_tokens("pom-whisper"))
        assert found["--vad"] is True and found["-vm"].endswith("ggml-silero-v6.2.0.bin") and found["-vt"] == "0.5"

    @pytest.mark.parametrize("name,slots", [("pom-scorer", "2"), ("pom-oracle", "1")])
    def test_the_slots_and_the_context_are_what_the_pi_was_benchmarked_with(self, name, slots):
        found = flags(exec_tokens(name))
        assert found["-np"] == slots and found["--ctx-size"] == "4096" and found["-t"] == "4" and found["--jinja"] is True

    def test_the_flags_agree_with_the_bench_script_that_starts_the_same_servers_on_the_mac(self, config):
        spec = importlib.util.spec_from_file_location("bench", ROOT / "scripts" / "dev" / "pipeline" / "run_printer_with_servers.py")
        bench = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bench)
        by_name = {server.name: flags(server.command) for server in bench.build_servers(config)}
        for unit, bench_name in (("pom-whisper", "whisper"), ("pom-scorer", "scorer"), ("pom-oracle", "oracle")):
            mine, theirs = flags(exec_tokens(unit)), by_name[bench_name]
            for flag in ("--host", "--port", "--ctx-size", "-np", "--jinja", "--vad", "-vt", "-t"):
                if flag in mine and flag in theirs:
                    assert mine[flag] == theirs[flag], f"{unit} and the bench script disagree on {flag}"
            assert {"--host", "--port"} <= set(mine) & set(theirs)


class TestThePrinterUnit:
    def test_it_comes_after_and_wants_all_three_servers(self):
        unit = read_unit("pom")["Unit"]
        for key in ("After", "Wants"):
            assert set(unit[key][0].split()) == {f"{name}.service" for name in SERVERS}

    def test_it_never_requires_a_server_so_a_crashed_server_cannot_take_it_down(self):
        unit = read_unit("pom")["Unit"]
        assert not ({"Requires", "BindsTo", "PartOf", "Requisite"} & set(unit))

    def test_it_runs_the_package_with_the_venv_python_and_a_source_path(self):
        service = read_unit("pom")["Service"]
        assert service["ExecStart"] == ["/home/pi/envs/pom/bin/python -m pom"]
        assert "PYTHONPATH=/home/pi/pom/src" in service["Environment"]

    def test_it_stops_cleanly_and_has_time_to_do_it(self):
        service = read_unit("pom")["Service"]
        assert service["KillSignal"] == ["SIGTERM"] and int(service["TimeoutStopSec"][0]) >= 10

    def test_its_log_is_flushed_as_it_is_written(self):
        assert "PYTHONUNBUFFERED=1" in read_unit("pom")["Service"]["Environment"]


class TestTheScripts:
    @pytest.mark.parametrize("script", ["install-services.sh", "update-pi.sh"])
    def test_it_is_executable_and_parses(self, script):
        path = SETUP / script
        assert os.access(path, os.X_OK)
        assert subprocess.run(["sh", "-n", str(path)], capture_output=True).returncode == 0

    def test_the_install_script_links_exactly_the_units_in_the_folder(self):
        text = (SETUP / "install-services.sh").read_text(encoding="utf-8")
        listed = re.search(r'^UNITS="([^"]*)"', text, re.MULTILINE).group(1).split()
        assert sorted(listed) == sorted(UNITS) == sorted(p.stem for p in (SETUP / "systemd").glob("*.service"))

    def test_it_links_not_copies_and_reloads_and_restarts_only_what_changed(self):
        text = (SETUP / "install-services.sh").read_text(encoding="utf-8")
        assert "systemctl link" in text and "daemon-reload" in text and "NeedDaemonReload" in text and "try-restart" in text
        assert re.search(r"^\s*(sudo )?cp ", text, re.MULTILINE) is None

    def test_the_install_script_checks_the_flags_before_it_changes_anything(self):
        text = (SETUP / "install-services.sh").read_text(encoding="utf-8")
        assert text.index('"$ROOT/scripts/setup/check-service-flags.sh"') < text.index('sudo systemctl link "')

    def test_it_refuses_to_run_as_root_or_from_another_checkout(self):
        text = (SETUP / "install-services.sh").read_text(encoding="utf-8")
        assert 'ROOT" != /home/pi/pom' in text and "-eq 0" in text

    def test_the_update_script_restarts_the_printer_only_for_code_settings_or_library(self):
        text = (SETUP / "update-pi.sh").read_text(encoding="utf-8")
        assert "--ff-only" in text and "install-services.sh" in text
        assert "src/|config/|library/|requirements" in text and "restart pom.service" in text


class TestTheLogs:
    def test_the_journal_is_kept_across_reboots_with_a_cap(self):
        text = (SETUP / "journald" / "90-pom.conf").read_text(encoding="utf-8")
        assert "Storage=persistent" in text and re.search(r"^SystemMaxUse=\d+M", text, re.MULTILINE)

    def test_the_install_script_flushes_the_journal_to_disk_after_restarting_journald(self):
        # Found on the Pi: the setting was in effect but the running journal stayed in /run until it was flushed.
        text = (SETUP / "install-services.sh").read_text(encoding="utf-8")
        assert text.index("restart systemd-journald") < text.index("journalctl --flush")

    def test_the_drop_in_sorts_after_the_one_raspberry_pi_os_ships_that_makes_it_volatile(self):
        assert "90-pom.conf" > "40-rpi-volatile-storage.conf"
        assert "90-pom.conf" in (SETUP / "install-services.sh").read_text(encoding="utf-8")


class TestTheFlagCheck:
    """check-service-flags.sh against a stand-in server whose --help lists some flags."""
    HELP = "usage: fakeserver\n  -m,  --model FILE   the model\n  -t,  --threads N    threads\n  -lm, --load-mode MODE   how to load\n      --jinja         templates\n"

    @staticmethod
    def fake_unit(tmp_path, flag_words, built=True):
        binary = tmp_path / "fakeserver"
        binary.write_text("#!/bin/sh\nprintf '%s' '" + TestTheFlagCheck.HELP.replace("'", "") + "'\n", encoding="utf-8")
        binary.chmod(0o755 if built else 0o644)
        unit = tmp_path / "pom-fake.service"
        unit.write_text(f"[Service]\nExecStart={binary} {flag_words}\n", encoding="utf-8")
        return unit

    @staticmethod
    def run(unit):
        return subprocess.run(["sh", str(SETUP / "check-service-flags.sh"), str(unit)], capture_output=True, text=True)

    def test_flags_the_server_lists_pass(self, tmp_path):
        result = self.run(self.fake_unit(tmp_path, "-m /x/model.gguf -t 4 -lm mmap+mlock --jinja"))
        assert result.returncode == 0 and "service flags ok" in result.stdout

    def test_a_flag_the_server_does_not_list_fails_and_is_named(self, tmp_path):
        result = self.run(self.fake_unit(tmp_path, "-m /x/model.gguf --mlock"))
        assert result.returncode == 1 and "does not list the flag --mlock" in result.stderr

    def test_a_flag_that_is_only_part_of_another_flag_is_not_listed(self, tmp_path):
        result = self.run(self.fake_unit(tmp_path, "-m /x/m.gguf --load"))
        assert result.returncode == 1 and "--load" in result.stderr

    def test_a_server_that_is_not_built_fails_and_says_where_to_build_it(self, tmp_path):
        result = self.run(self.fake_unit(tmp_path, "-t 4", built=False))
        assert result.returncode == 1 and "is not built" in result.stderr

    def test_values_that_are_not_flags_are_not_checked(self, tmp_path):
        assert self.run(self.fake_unit(tmp_path, "-t 4 -m /x/-weird-name.gguf")).returncode == 0

    def test_by_default_it_checks_the_three_servers_and_not_the_printer(self):
        text = (SETUP / "check-service-flags.sh").read_text(encoding="utf-8")
        assert all(f"systemd/pom-{name}.service" in text for name in ("whisper", "scorer", "oracle"))
        assert "systemd/pom.service" not in text
