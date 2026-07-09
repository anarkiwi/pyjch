"""Exceptions raised by pyjch."""

from pysidtracker import SidError


class JCHError(SidError):
    """Base class for all pyjch errors."""


class SidParseError(JCHError):
    """A PSID/RSID/PRG image (or byte string) could not be parsed."""
