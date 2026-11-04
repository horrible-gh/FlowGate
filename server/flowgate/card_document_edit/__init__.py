"""Shared card document read/revision boundary (0127 L#1, P#1)."""
from .contracts import Actor, Origin, ReviseRequest, Outcome
from .revise import revise
from .read import read_card, snapshot

__all__ = ["Actor", "Origin", "ReviseRequest", "Outcome", "revise", "read_card", "snapshot"]
