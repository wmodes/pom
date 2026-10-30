"""The output module: making a portent printable and sending it to the typewriter."""
from pom.output.typewriter_formatter import TypewriterFormatter
from pom.output.typewriter_port import ConsoleTypewriter, TypewriterPort, TypewriterPortError, TypingReport

__all__ = ["ConsoleTypewriter", "TypewriterFormatter", "TypewriterPort", "TypewriterPortError", "TypingReport"]
