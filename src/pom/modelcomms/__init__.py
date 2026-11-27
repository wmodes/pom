"""The modelcomms module: how the Printer talks to the servers of its three models (whisper, the scorer, the oracle).

ModelComms carries one model's requests and says whether its server is up. The model clients, in their own stages (listening, scoring,
writing), say what to ask and what the answer means.
"""
from pom.modelcomms.model_comms import ModelComms

__all__ = ["ModelComms"]
