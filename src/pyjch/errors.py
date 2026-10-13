"""Exceptions for pyjch, built by :func:`pysidtracker.make_package_errors`.

``JCHParseError`` subclasses both ``JCHError`` and
:class:`pysidtracker.SidParseError` ("not a decodable JCH NewPlayer tune");
``SidParseError`` is kept as its backward-compatible alias.
"""

from pysidtracker import make_package_errors

JCHError, JCHParseError, SidFormatError = make_package_errors("JCH")
SidParseError = JCHParseError

__all__ = ["JCHError", "JCHParseError", "SidFormatError", "SidParseError"]
