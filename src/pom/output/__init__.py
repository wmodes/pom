"""The output module: making a portent printable and sending it to the typewriter."""
from pom.output.portent_formatter import PortentFormatter
from pom.output.terminal_port import ConsoleTerminal, TerminalPort, TerminalPortError, PrintResult

__all__ = ["ConsoleTerminal", "PortentFormatter", "TerminalPort", "TerminalPortError", "PrintResult"]
