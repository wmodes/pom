"""Guards for the one shadow of pom.toml (Design Audit 03 D5): the settings' defaults live in pom.defaults and nowhere else, and every
setting the code reads is declared there.

They read the code itself (with `ast`), so a copy of a default slipped back into a module, or a setting read under a name nobody
declared, fails here instead of quietly taking a value no one chose.
"""
import ast
import dataclasses
from pathlib import Path

import pytest

from pom.defaults import SECTIONS

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "pom"
WHERE_SETTINGS_ARE_READ = [SOURCE, ROOT / "scripts"]
WHERE_DEFAULTS_MAY_LIVE = {"defaults.py", "config.py"}

# Module-level DEFAULT_ names that are not copies of a pom.toml setting, each with why it is still here. The list may only shrink: a name
# that is gone from the code must be taken off it (the last test below), and a new one fails until it is added here with a reason.
NOT_SETTINGS = {
    "printer.py": {"DEFAULT_CONFIG": "the path of pom.toml itself"},
    "qualifying/decaying_quantile_bar.py": {"DEFAULT_STARTED_AT": "a stream time, not a setting"},
    "qualifying/moving_average_bar.py": {"DEFAULT_SCORE_RANGE": "the scorer's digits 0 to 9, an argument; Design Audit 03 D6.4"},
    "monitor/chart_notebook.py": {"DEFAULT_SILENCE_SHAPE": "for a notebook built without one; the Printer hands it silence_shape(config.chart)"},
    "listening/mic_deck.py": {"DEFAULT_DEVICE": "the deck's own device when none is given; [listening] device is looked up before it"},
    "listening/mac_mic_deck.py": {"DEFAULT_MAC_DEVICE": "the same, on the Mac"},
    "listening/wav_deck.py": {"DEFAULT_SPEED": "a testbench flag, not a setting", "DEFAULT_END_PAUSE_SECONDS": "Design Audit 03 D6.1"},
    "library/circulation.py": {"DEFAULT_WEIGHT": "a shelf's share in the circulation file, not pom.toml (hidden value, to decide)"},
    "scoring/fallback_scorer.py": {"DEFAULT_BASE": "the fallback scorer's base (hidden value, to decide)"},
    "scoring/word_tagger.py": {"DEFAULT_FILLERS": "the filler words; Design Audit 03 D6.3"},
    "scoring/scorer_model_client.py": {"DEFAULT_EXPLAIN_TIMEOUT": "the explain request, Design Audit 03 D2.3"},
    "conductor/keyboard_watcher.py": {"DEFAULT_POLL_SECONDS": "Design Audit 03 D6.1", "DEFAULT_ERROR_PAUSE_SECONDS": "Design Audit 03 D6.1"},
}

# The attribute a class keeps a section's settings under, where it is not the section's own name (so it does not read `conductor.conductor`).
SETTINGS_ATTRIBUTES = {**{name: name for name in SECTIONS}, "scorer_settings": "scorer", "conductor_settings": "conductor"}
# Where a class keeps something else under a section's name: the Printer's `self.conductor` is the Conductor, not its settings.
NOT_SETTINGS_ATTRIBUTES = {"printer.py": {"conductor"}}


def python_files(*folders):
    return sorted(path for folder in folders for path in folder.rglob("*.py") if "__pycache__" not in path.parts)


def module_defaults(path):
    """The module-level names with DEFAULT in them that are given a value in this file."""
    names = set()
    for node in ast.parse(path.read_text()).body:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
        names |= {target.id for target in targets if isinstance(target, ast.Name) and "DEFAULT" in target.id}
    return names


def found_defaults():
    found = {}
    for path in python_files(SOURCE):
        if path.name in WHERE_DEFAULTS_MAY_LIVE:
            continue
        names = module_defaults(path)
        if names:
            found[path.relative_to(SOURCE).as_posix()] = names
    return found


def test_no_module_keeps_a_default_of_its_own():
    """A DEFAULT_ constant is how a hidden copy of a setting looked before D5; pom.defaults is the one place a setting's default is said."""
    unexplained = {where: sorted(names - set(NOT_SETTINGS.get(where, {}))) for where, names in found_defaults().items()}
    assert not {where: names for where, names in unexplained.items() if names}, \
        "a default belongs in pom.defaults; if this one is not a pom.toml setting, add it to NOT_SETTINGS with the reason"


def test_the_list_of_exceptions_has_nothing_that_is_gone():
    found = found_defaults()
    stale = {where: sorted(set(names) - found.get(where, set())) for where, names in NOT_SETTINGS.items()}
    assert not {where: names for where, names in stale.items() if names}, "take these off NOT_SETTINGS: the code no longer has them"


def settings_reads(path):
    """Every `self.<section>.<name>` and `config.<section>.<name>` in a file, as (line, section, name)."""
    reads, elsewhere = [], NOT_SETTINGS_ATTRIBUTES.get(path.name, set())
    for node in ast.walk(ast.parse(path.read_text())):
        if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Attribute) and isinstance(node.value.value, ast.Name)
                and node.value.value.id in ("self", "config") and node.value.attr in SETTINGS_ATTRIBUTES
                and not (node.value.value.id == "self" and node.value.attr in elsewhere)):
            reads.append((node.lineno, SETTINGS_ATTRIBUTES[node.value.attr], node.attr))
    return reads


def is_declared(section, name):
    settings_class = SECTIONS[section]
    return name in {f.name for f in dataclasses.fields(settings_class)} or isinstance(getattr(settings_class, name, None), property)


@pytest.mark.parametrize("path", python_files(*WHERE_SETTINGS_ARE_READ), ids=lambda path: path.relative_to(ROOT).as_posix())
def test_every_setting_the_code_reads_is_declared_in_the_defaults(path):
    """A setting read here but not declared in pom.defaults would have no default, no rule and no place in pom.toml."""
    undeclared = [f"line {line}: {section}.{name}" for line, section, name in settings_reads(path)
                  if not is_declared(section, name)]
    assert not undeclared, f"{path.relative_to(ROOT)} reads settings pom.defaults does not declare: {undeclared}"


def test_the_scan_finds_the_reads_it_is_looking_for():
    """So the test above cannot pass by finding nothing: the page reads the chart's window as `self.chart.window_minutes`."""
    assert ("chart", "window_minutes") in {(section, name) for _, section, name in settings_reads(SOURCE / "monitor" / "monitor_site.py")}
