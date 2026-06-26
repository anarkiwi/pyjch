"""Exceptions raised by pyjch."""


class JCHError(Exception):
    """Base class for all pyjch errors."""


class SidParseError(JCHError):
    """A PSID/RSID/PRG image (or byte string) could not be parsed."""
