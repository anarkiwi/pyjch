"""Byte grammar of the song streams the JCH NewPlayer wavetable family reads.

``newplayer.lark`` holds the grammar; this module binds it to models:

* ``orderlist``: ``$80``-``$FD`` set the next step's transpose (the last one
  before a pattern index wins), ``$00``-``$7F`` is a pattern index, ``$FE``
  stops and ``$FF`` loops;
* ``pattern``: ``$00``-``$7E`` notes, ``$80``-``$FF`` commands, ``$7F`` ends;
* ``pattern_v1``: the V1/V2 (interleaved wave stream) patterns, which end at ``$FF``;
* ``instrument``: an 8-byte record; ``step``: a 4-byte pulse/filter record.
"""

from dataclasses import dataclass
from typing import Tuple

from lark import Transformer
from pysidtracker import ByteGrammar, Parsed, SidImage, select_rule

from pyjch.errors import SidParseError

ORDERLIST = "orderlist"
PATTERN = "pattern"
PATTERN_V1 = "pattern_v1"
INSTRUMENT = "instrument"
STEP = "step"


@dataclass(frozen=True)
class OrderStream:
    """An order list: ``(pattern, transpose)`` steps, whether it loops, its bytes."""

    steps: Tuple[Tuple[int, int], ...]
    loops: bool
    raw: bytes

    def to_bytes(self) -> bytes:
        """The stream as stored, terminator included."""
        return self.raw


@dataclass(frozen=True)
class PatternStream:
    """A pattern: its note/command bytes and the stream as stored."""

    events: bytes
    raw: bytes

    def to_bytes(self) -> bytes:
        """The stream as stored, terminator included."""
        return self.raw


@dataclass(frozen=True)
class Record:
    """A fixed-width table record."""

    raw: bytes

    def to_bytes(self) -> bytes:
        """The record as stored."""
        return self.raw


def _span(tokens) -> bytes:
    return bytes(ord(tok) for tok in tokens)


class _Streams(Transformer):
    # pylint: disable=missing-function-docstring
    def orderlist(self, tokens):
        steps = []
        transpose = 0
        for tok in tokens[:-1]:
            if tok.type == "TRANSPOSE":
                transpose = ord(tok)
            else:
                steps.append((ord(tok), transpose))
                transpose = 0
        return Parsed(
            OrderStream(tuple(steps), tokens[-1].type == "LOOP", _span(tokens)),
            tokens[-1].end_pos,
        )

    def pattern(self, tokens):
        return Parsed(
            PatternStream(_span(tokens[:-1]), _span(tokens)), tokens[-1].end_pos
        )

    pattern_v1 = pattern

    def instrument(self, tokens):
        return Parsed(Record(_span(tokens)), tokens[-1].end_pos)

    def step(self, tokens):
        return Parsed(Record(_span(tokens)), tokens[-1].end_pos)


GRAMMAR = ByteGrammar(
    "pyjch",
    "newplayer.lark",
    (ORDERLIST, PATTERN, PATTERN_V1, INSTRUMENT, STEP),
    _Streams(),
    error=SidParseError,
)

ORDERLIST_REACH = 512
PATTERN_REACH = 1024


def read_stream(img: SidImage, addr: int, rule: str, what: str, size: int):
    """The ``rule`` stream at ``addr`` of ``img`` (a parse error if malformed)."""
    return GRAMMAR.read(img, addr, rule, what, size).model


def pattern_rule(model) -> str:
    """The pattern start rule of ``model``'s build."""
    return select_rule(
        {"interleaved_wave": model.wave_stream is not None},
        (("interleaved_wave", PATTERN_V1),),
        PATTERN,
    )
