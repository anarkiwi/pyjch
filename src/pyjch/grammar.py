"""Byte grammar of the song streams the JCH NewPlayer wavetable family reads.

``newplayer.lark`` holds the grammar; this module binds it to models:

* ``orderlist``: ``$80``-``$FD`` set the next step's transpose (the last one
  before a pattern index wins), ``$00``-``$7F`` is a pattern index, ``$FE``
  stops and ``$FF`` loops;
* ``pattern``: ``$00``-``$7E`` notes, ``$80``-``$FF`` commands, ``$7F`` ends;
* ``pattern_v1``: the V1/V2 (interleaved wave stream) patterns, which end at ``$FF``;
* ``v0x_orderlist`` (V0x): ``$00``-``$7F`` run subpattern ``n``, ``$80``-``$9F`` set
  the transpose, ``$A0``-``$FF`` restart the list;
* ``v0x_subpattern`` (V0x): ``$00`` rest, ``$01``-``$7E`` note, ``$7F`` tie,
  ``$80``-``$8F`` set the note duration, ``$90``-``$FF`` return to the order list;
* ``instrument``: an 8-byte record (AD, SR, wave flags, filter mode, filter start,
  pulse start, wave start, alternate wave start);
* ``wave_program``: the rows of a wave table's note column from an instrument's wave
  start: ``$00``-``$7D`` / ``$80``-``$FF`` a row, ``$7E`` holds the last row, ``$7F``
  jumps to the control column's target;
* ``pulse_table`` / ``filter_table``: 4-byte steps (value or ``$FF`` to keep, step,
  direction + rate or dwell, next step); ``command_table``: 2-byte entries whose first
  byte's high nibble selects the kind.
"""

from dataclasses import astuple, dataclass
from typing import Iterator, Tuple

from lark import Transformer
from pysidtracker import ByteGrammar, Parsed, SidImage, select_rule
from pysidtracker.bytegrammar import REACH


from pyjch.errors import SidParseError
from pyjch.newplayer import image_of
from pyjch.songmodel import CommandKind

ORDERLIST = "orderlist"
PATTERN = "pattern"
PATTERN_V1 = "pattern_v1"
V0X_ORDERLIST = "v0x_orderlist"
V0X_SUBPATTERN = "v0x_subpattern"
INSTRUMENT = "instrument"
WAVE_PROGRAM = "wave_program"
PULSE_TABLE = "pulse_table"
FILTER_TABLE = "filter_table"
COMMAND_TABLE = "command_table"


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
class V0xStream:
    """A V0x order list or subpattern: its opcode bytes and the stream as stored."""

    events: bytes
    raw: bytes

    def to_bytes(self) -> bytes:
        """The stream as stored, terminator included."""
        return self.raw


@dataclass(frozen=True)
class WaveProgram:
    """A wave-table program: its rows, the byte that ends it, and the stream."""

    rows: bytes
    end: int
    raw: bytes

    def to_bytes(self) -> bytes:
        """The program as stored, terminator included."""
        return self.raw


@dataclass(frozen=True)
class StepTable:
    """A pulse or filter table: its 4-byte steps."""

    steps: Tuple[Tuple[int, int, int, int], ...]
    raw: bytes

    def to_bytes(self) -> bytes:
        """The table as stored."""
        return self.raw


@dataclass(frozen=True)
class CommandTable:
    """A command table: ``(kind, first byte, second byte)`` per entry."""

    entries: Tuple[Tuple[str, int, int], ...]
    raw: bytes

    def to_bytes(self) -> bytes:
        """The table as stored."""
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


def _steps(tokens) -> StepTable:
    raw = _span(tokens)
    return StepTable(
        tuple(tuple(raw[at : at + 4]) for at in range(0, len(raw), 4)), raw
    )


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

    def v0x_orderlist(self, tokens):
        return Parsed(V0xStream(_span(tokens[:-1]), _span(tokens)), tokens[-1].end_pos)

    v0x_subpattern = v0x_orderlist

    def wave_program(self, tokens):
        return Parsed(
            WaveProgram(_span(tokens[:-1]), ord(tokens[-1]), _span(tokens)),
            tokens[-1].end_pos,
        )

    def pulse_table(self, tokens):
        return Parsed(_steps(tokens), tokens[-1].end_pos)

    filter_table = pulse_table

    def command(self, tokens):
        return tokens[0].type[4:].lower(), ord(tokens[0]), ord(tokens[1])

    def command_table(self, entries):
        raw = bytes(
            byte for _kind, first, second in entries for byte in (first, second)
        )
        return Parsed(CommandTable(tuple(entries), raw), len(raw))

    def instrument(self, tokens):
        return Parsed(Record(_span(tokens)), tokens[-1].end_pos)


GRAMMAR = ByteGrammar(
    "pyjch",
    "newplayer.lark",
    (
        ORDERLIST,
        PATTERN,
        PATTERN_V1,
        V0X_ORDERLIST,
        V0X_SUBPATTERN,
        INSTRUMENT,
        WAVE_PROGRAM,
        PULSE_TABLE,
        FILTER_TABLE,
        COMMAND_TABLE,
    ),
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


def v0x_streams(song):
    """``(name, model, rule)`` of the order lists and subpatterns of ``song``'s subtune."""
    img = image_of(song.load_addr, song.image)
    end = song.load_addr + len(song.image)
    refs = set()
    for voice in range(3):
        addr = song.orderlist_ptr(song.subtune, voice)
        name = f"order list {song.subtune}.{voice}"
        order = GRAMMAR.read(img, addr, V0X_ORDERLIST, name, min(REACH, end - addr))
        refs.update(byte for byte in order.model.events if byte < 0x80)
        yield name, order.model, V0X_ORDERLIST
    for ref in sorted(refs):
        addr = song.subpattern_ptr(ref)
        name = f"subpattern {ref}"
        sub = GRAMMAR.read(img, addr, V0X_SUBPATTERN, name, min(REACH, end - addr))
        yield name, sub.model, V0X_SUBPATTERN


def _selected_wave_starts(tune) -> set:
    """The wave starts the patterns of ``tune`` select: instruments and wave patches."""
    starts = set()
    for events in tune.patterns:
        for event in events:
            cmd = event.command
            if cmd is None:
                continue
            if cmd.kind is CommandKind.INSTRUMENT and cmd.arg < len(tune.instruments):
                inst = tune.instruments[cmd.arg]
                starts.update((inst.wave_start, inst.wave_start2))
            elif cmd.kind is CommandKind.WAVE_PATCH and cmd.param_hi is not None:
                starts.add(cmd.param_hi)
    return starts


def wave_streams(tune) -> Iterator[tuple]:
    """``(name, model, rule)`` of each wave program the patterns of ``tune`` start.

    Only a V20-tier tune is read this way: the instrument record's wave-start fields
    are at the V20 offsets, which other family builds do not share.  The implicit
    first instrument is left out: whether a voice plays it depends on the order of
    play, which :class:`~pyjch.player.JchPlayer` checks as it runs (``validate``).
    """
    table = tune.wavetable
    if tune.provenance.tier != "v20" or table is None or not table.note_col:
        return
    column = bytes(table.note_col)
    for start in sorted(_selected_wave_starts(tune)):
        name = f"wave program {start}"
        if start >= len(column):
            raise SidParseError(f"{name} starts past the {len(column)}-row note column")
        try:
            program = GRAMMAR.parse_prefix(column[start:], WAVE_PROGRAM)
        except SidParseError as exc:
            raise SidParseError(f"{name}: {exc}") from exc
        yield name, program.model, WAVE_PROGRAM


def _parse_named(name: str, data: bytes, rule: str):
    try:
        return GRAMMAR.parse_exact(bytes(data), rule)
    except SidParseError as exc:
        raise SidParseError(f"{name}: {exc}") from exc


def tune_streams(tune, pattern: str = PATTERN) -> Iterator[tuple]:
    """``(name, model, rule)`` of every stream and table of a recovered ``tune``.

    Raises :class:`~pyjch.errors.SidParseError` naming the first one that is malformed.
    """
    for index, sub in enumerate(tune.subtunes):
        for voice, order in enumerate(sub.order_lists):
            name = f"order list {index}.{voice}"
            yield name, _parse_named(name, order.raw, ORDERLIST), ORDERLIST
    for index, raw in enumerate(tune.pattern_raw):
        if raw:
            name = f"pattern {index}"
            yield name, _parse_named(name, raw, pattern), pattern
    for index, inst in enumerate(tune.instruments):
        name = f"instrument {index}"
        yield name, _parse_named(name, inst.raw, INSTRUMENT), INSTRUMENT
    yield from wave_streams(tune)
    for name, rule, steps in (
        ("pulse table", PULSE_TABLE, tune.pw_program),
        ("filter table", FILTER_TABLE, tune.filter_program),
    ):
        if steps:
            raw = bytes(byte for step in steps for byte in astuple(step))
            yield name, _parse_named(name, raw, rule), rule
    if tune.commands:
        raw = bytes(byte for cmd in tune.commands for byte in (cmd.lo, cmd.hi))
        yield "command table", _parse_named("command table", raw, COMMAND_TABLE), (
            COMMAND_TABLE
        )
