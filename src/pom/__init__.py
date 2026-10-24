"""Printer of Mysteries: runtime code for the haunted typewriter.

Everything here is object-oriented: small classes with one concern each, passing plain data
objects to each other. Helper scripts (corpus building, setup) live in scripts/ and may import
from this package, never the reverse.

PROJECT-WIDE PRINCIPLE: the Printer must never stop because of a typo or a recoverable fault.
A bad number in a hand-edited file, one corrupt line in a corpus file, a missing optional file,
a mic that drops out: none of these may crash the oracle in a gallery. A module that meets a
problem it can recover from recovers (falls back to a default, skips the bad entry, retries) and
records what it did in a `FaultRecorder` and in the log (stdout, so journald) for a person to
read later. Only a problem that
makes a module unable to do its job at all is CRITICAL, and even then the module returns an empty
result and says so, and the Conductor degrades (stays quiet and keeps listening) instead of
crashing. `strict=True` turns every fault back into an exception, for tests and dev tools, so
typos are loud while you work and silent in the gallery.
"""
