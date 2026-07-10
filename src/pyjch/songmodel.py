"""Playroutine-independent song model for the JCH NewPlayer family.

A :class:`Tune` is the neutral, serialization-friendly result of
:func:`pyjch.extract.extract`: metadata plus the recovered tables (subtunes /
order lists / patterns / instruments / wavetable / pulse + filter programs /
groove / pitch / commands), decoded from the packed player image but carrying
no player-code assumptions.  Every field is a plain ``int``/``bool``/``str``,
a ``list`` of those, a nested dataclass, or a ``str``-valued :class:`enum.Enum`,
so :func:`dataclasses.asdict` round-trips it through JSON losslessly.

:class:`Provenance` records the decode ``tier`` (``"v20"`` = full V20 decode,
``"family"`` = the family subset), the discovered table bases, and honest notes
for any table left absent -- the model never fabricates a table it cannot ground
in the recovered player data.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


class NoteKind(str, Enum):
    """A pattern note event: a played note, a rest/tie, or a gate hold."""

    NOTE = "note"
    REST = "rest"
    HOLD = "hold"


class CommandKind(str, Enum):
    """A pattern command event, classified as the player classifies it."""

    DURATION = "duration"
    INSTRUMENT = "instrument"
    PORTAMENTO = "portamento"
    VIBRATO = "vibrato"
    TEMPO = "tempo"
    VOLUME = "volume"
    AD_OVERRIDE = "ad_override"
    WAVE_PATCH = "wave_patch"


@dataclass
class NoteEvent:
    """Decoded note byte: ``kind`` plus the raw note ``value``."""

    kind: NoteKind
    value: int


@dataclass
class CommandEvent:
    """Decoded command byte.

    ``byte`` is the raw command byte; ``arg``/``delay`` carry the inline
    duration/instrument operands; ``cmd_index`` plus ``param_lo``/``param_hi``
    resolve a command-table (``$C0``-``$FF``) entry.  ``kind`` is ``None`` when a
    ``$C0`` command cannot be classified (family tier, command table absent).
    """

    kind: Optional[CommandKind]
    byte: int
    arg: Optional[int] = None
    delay: Optional[bool] = None
    cmd_index: Optional[int] = None
    param_lo: Optional[int] = None
    param_hi: Optional[int] = None


@dataclass
class PatternEvent:
    """One decoded pattern byte: ``raw`` plus exactly one of note/command."""

    raw: int
    note: Optional[NoteEvent] = None
    command: Optional[CommandEvent] = None


@dataclass
class OrderEntry:
    """One order-list step: a ``pattern`` index and its ``transpose`` prefix."""

    pattern: int
    transpose: int = 0


@dataclass
class OrderList:
    """A voice order list: its entries, whether it loops, and the restart step."""

    entries: List[OrderEntry] = field(default_factory=list)
    loops: bool = False
    restart: int = 0


@dataclass
class Subtune:
    """A subtune: three voice order lists and a tempo/groove reload byte."""

    order_lists: List[OrderList]
    tempo: int


@dataclass
class Instrument:
    """An 8-byte instrument record decoded into named fields.

    ``raw`` keeps the source bytes so the record re-serializes byte-identically;
    the remaining fields are the meanings the V20 instrument-init assigns.
    """

    raw: List[int]
    ad: int
    sr: int
    wave_speed: int
    abs_freq: bool
    flag7: bool
    filter_mode: int
    filter_route: int
    filter_prog: int
    pw_prog: int
    wave_start: int
    wave_start2: int


@dataclass
class WaveTable:
    """The two parallel wavetable columns (note/freq and waveform/CTRL)."""

    note_col: List[int]
    ctrl_col: List[int]


@dataclass
class PwStep:
    """A 4-byte pulse-program record (reset, step, dir+rate, next offset)."""

    reset: int
    step: int
    dir_rate: int
    next_off: int


@dataclass
class FilterStep:
    """A 4-byte filter-program record (value, step, dwell, next offset)."""

    value: int
    step: int
    dwell: int
    next_off: int


@dataclass
class Command:
    """A command-table entry (``lo``/``hi`` param bytes) and its classified kind."""

    lo: int
    hi: int
    kind: Optional[CommandKind]


@dataclass
class Provenance:
    """Decode provenance: tier, family/version tag, bases, and honest notes."""

    tier: str
    version: str
    bases: Dict[str, int] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)


@dataclass
class Tune:
    """A recovered, playroutine-independent JCH NewPlayer song."""

    name: str
    author: str
    released: str
    load_addr: int
    init_addr: int
    play_addr: int
    hard_restart: Optional[int]
    subtunes: List[Subtune]
    patterns: List[List[PatternEvent]]
    instruments: List[Instrument]
    wavetable: Optional[WaveTable]
    pw_program: List[PwStep]
    filter_program: List[FilterStep]
    groove: List[int]
    pitch_table: List[int]
    commands: List[Command]
    provenance: Provenance
