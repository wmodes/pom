"""The voice module: asking the oracle model for a portent."""
from pom.voice.oracle_model_client import OracleModelClient, OracleModelClientError
from pom.voice.portent_writer import Portent, PortentWriter, PortentWriterError

__all__ = ["OracleModelClient", "OracleModelClientError", "Portent", "PortentWriter", "PortentWriterError"]
