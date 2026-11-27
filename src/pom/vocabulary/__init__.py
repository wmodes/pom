"""The vocabulary module: the small shared words the Printer's parts use to talk to each other, each in a module of its own.

The Lexicon (documentation/Lexicon.md) defines the words; this folder holds them in code. A word lives here, not in the module of the part
that makes it, so any part can use it without importing that part (and everything it imports). Each word's module imports nothing of the
Printer's.
"""
from pom.vocabulary.monitor_light import MonitorLight

__all__ = ["MonitorLight"]
