"""The typed JCH NewPlayer song, in the player's own terms.

Each value is the meaning the NewPlayer code gives its byte, bits a build
ignores are kept as ``reserved``, and no address or memory image is part of the
song, so :func:`encode` reproduces the song data that :func:`decode` read.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple, Union

from pyjch.errors import SidParseError
from pyjch.model import Song as V0xLayout
from pyjch.player import discover_bases


class Dialect(str, Enum):
    """The byte vocabulary of tracks and sequences."""

    V0X = "v0x"  # $80+ transpose / $A0+ loop; $8x duration, $90+ end, $7F hold
    NEWPLAYER = "newplayer"  # $80+ transpose, $FE/$FF; $7F end, $7E hold


# ---- tracks ---------------------------------------------------------------
@dataclass(frozen=True)
class PlaySequence:
    """Play one sequence, at the track's current transpose."""

    sequence: int


@dataclass(frozen=True)
class Transpose:
    """Transpose the following sequences by ``semitones``."""

    semitones: int


@dataclass(frozen=True)
class Loop:
    """Restart the track."""


@dataclass(frozen=True)
class Stop:
    """Stop the track."""


TrackOp = Union[PlaySequence, Transpose]


@dataclass(frozen=True)
class Track:
    """One voice's order list: its ops and how it ends."""

    ops: Tuple[TrackOp, ...]
    end: Union[Loop, Stop]


# ---- sequences ------------------------------------------------------------
@dataclass(frozen=True)
class Note:
    """Key a note (``pitch`` before the track transpose)."""

    pitch: int


@dataclass(frozen=True)
class GateOff:
    """Release the gate."""


@dataclass(frozen=True)
class Hold:
    """Keep the gate open; no new note."""


@dataclass(frozen=True)
class Duration:
    """The rows this and the following events last; ``tie`` skips the note-on."""

    rows: int
    tie: bool = False


@dataclass(frozen=True)
class SelectInstrument:
    """Select an instrument for the following notes."""

    instrument: int


@dataclass(frozen=True)
class RunCommand:
    """Run an entry of the command table."""

    command: int


Prefix = Union[Duration, SelectInstrument, RunCommand]
Event = Union[Note, GateOff, Hold]


@dataclass(frozen=True)
class Row:
    """The prefixes a row carries, in order, then its event (``None`` at the end)."""

    prefix: Tuple[Prefix, ...]
    event: Optional[Event]


@dataclass(frozen=True)
class Sequence:
    """A sequence's rows."""

    rows: Tuple[Row, ...]


# ---- commands -------------------------------------------------------------
@dataclass(frozen=True)
class Slide:
    """Slide the frequency by ``speed`` a frame, ``down`` or up."""

    down: bool
    speed: int
    reserved: int = 0


@dataclass(frozen=True)
class Vibrato:
    """Vibrato: ``speed`` per step, turning every ``delay`` frames, at ``depth``."""

    speed: int
    delay: int
    depth: int


@dataclass(frozen=True)
class SetSustainRelease:
    """Override the next instrument's sustain/release."""

    value: int
    reserved: int = 0


@dataclass(frozen=True)
class SetTempo:
    """Set the row speed."""

    value: int
    reserved: int = 0


@dataclass(frozen=True)
class SetVolume:
    """Set the master volume byte (the filter mode is OR'd over it)."""

    value: int
    reserved: int = 0


@dataclass(frozen=True)
class SetInstrumentWave:
    """Point an instrument's wave and release-wave rows at ``wave``."""

    instrument: int
    wave: int
    reserved: int = 0


Command = Union[
    Slide, Vibrato, SetSustainRelease, SetTempo, SetVolume, SetInstrumentWave
]
_VALUE_COMMANDS = {SetSustainRelease: 0x90, SetTempo: 0xE0, SetVolume: 0xF0}


# ---- instruments ----------------------------------------------------------
@dataclass(frozen=True)
class Instrument:
    """An instrument record; the three programs are byte offsets into their tables.

    ``hard_restart`` arms the gate-off re-arm, ``absolute`` reads wave notes as
    pitches, ``filter_mode`` 0 leaves the filter, 8 keeps its program, else sets it.
    """

    # pylint: disable=too-many-instance-attributes  # one field per record field
    attack_decay: int
    sustain_release: int
    hard_restart: bool
    absolute: bool
    wave_speed: int
    filter_resonance: int
    filter_mode: int
    filter_program: int
    pulse_program: int
    wave: int
    wave_release: int
    reserved: int = 0


# ---- tables ---------------------------------------------------------------
@dataclass(frozen=True)
class WaveStep:
    """Set the waveform and play ``note``: relative to the key, or ``absolute``."""

    wave: int
    note: int
    absolute: bool = False


@dataclass(frozen=True)
class WaveHold:
    """Stay on the previous row (``wave`` is never read)."""

    wave: int


@dataclass(frozen=True)
class WaveJump:
    """Continue at ``row``."""

    row: int


WaveRow = Union[WaveStep, WaveHold, WaveJump]


@dataclass(frozen=True)
class PulseStep:
    """Set the width (unless ``None``), add ``add`` a frame for ``frames``, go ``next``."""

    width: Optional[int]
    add: int
    down: bool
    frames: int
    next: int


@dataclass(frozen=True)
class FilterStep:
    """Set the cutoff (unless ``None``), add ``add`` a frame for ``frames``, go ``next``."""

    cutoff: Optional[int]
    add: int
    frames: int
    next: int


@dataclass(frozen=True)
class FilterTable:
    """The filter program; its first record is also the groove and the filter's voice."""

    steps: Tuple[FilterStep, ...]

    @property
    def groove(self) -> Tuple[int, int]:
        """The two funk-tempo speeds (the first record's first two bytes)."""
        first = filter_bytes(self.steps[0])
        return first[0], first[1]

    @property
    def voice(self) -> int:
        """The voice whose tick runs the filter program."""
        return self.steps[0].next


@dataclass(frozen=True)
class Subtune:
    """A subtune: its tempo, the record's flag byte and one track per voice."""

    tempo: int
    flags: Optional[int]
    tracks: Tuple[Track, ...]


@dataclass(frozen=True)
class JchSong:
    """A JCH NewPlayer song; tables a build does not have are ``None``."""

    # pylint: disable=too-many-instance-attributes  # one field per table
    dialect: Dialect
    subtunes: Tuple[Subtune, ...]
    sequences: Dict[int, Sequence]
    instruments: Tuple[Instrument, ...]
    commands: Optional[Tuple[Command, ...]]
    wave: Optional[Tuple[WaveRow, ...]]
    pulse: Optional[Tuple[PulseStep, ...]]
    filter: Optional[FilterTable]
    pitch: Tuple[int, ...]


# ---- byte vocabulary ------------------------------------------------------
def _track(dialect: Dialect, data: List[int]) -> Track:
    ops: List[TrackOp] = []
    for byte in data[:-1]:
        if byte < 0x80:
            ops.append(PlaySequence(byte))
        elif dialect is Dialect.V0X:
            ops.append(Transpose((byte & 0x1F) - 0x0C))
        else:
            ops.append(Transpose(byte & 0x7F))
    stop = dialect is Dialect.NEWPLAYER and data[-1] == 0xFE
    return Track(tuple(ops), Stop() if stop else Loop())


def track_bytes(dialect: Dialect, track: Track) -> List[int]:
    """A track's bytes, through its end."""
    v0x = dialect is Dialect.V0X
    out = [
        (
            op.sequence
            if isinstance(op, PlaySequence)
            else 0x80 | (op.semitones + 0x0C if v0x else op.semitones)
        )
        for op in track.ops
    ]
    return out + [0xFE if isinstance(track.end, Stop) else 0xFF]


def _prefix(dialect: Dialect, byte: int) -> Prefix:
    if dialect is Dialect.V0X or byte & 0xE0 == 0x80:
        return Duration(byte & 0x0F, dialect is Dialect.NEWPLAYER and bool(byte & 0x10))
    if byte & 0xE0 == 0xA0:
        return SelectInstrument(byte & 0x1F)
    return RunCommand(byte & 0x3F)


def _sequence(dialect: Dialect, data: List[int]) -> Sequence:
    hold = 0x7F if dialect is Dialect.V0X else 0x7E
    rows, prefix = [], []
    for byte in data[:-1]:
        if byte & 0x80:
            prefix.append(_prefix(dialect, byte))
            continue
        rows.append(
            Row(tuple(prefix), {0: GateOff(), hold: Hold()}.get(byte, Note(byte)))
        )
        prefix = []
    if prefix:
        rows.append(Row(tuple(prefix), None))
    return Sequence(tuple(rows))


def _prefix_byte(prefix: Prefix) -> int:
    if isinstance(prefix, Duration):
        return 0x80 | (0x10 if prefix.tie else 0) | prefix.rows
    if isinstance(prefix, SelectInstrument):
        return 0xA0 | prefix.instrument
    return 0xC0 | prefix.command


def sequence_bytes(dialect: Dialect, seq: Sequence) -> List[int]:
    """A sequence's bytes, through its end."""
    hold, end = (0x7F, 0xFF) if dialect is Dialect.V0X else (0x7E, 0x7F)
    out: List[int] = []
    for row in seq.rows:
        out += [_prefix_byte(p) for p in row.prefix]
        if row.event is not None:
            out.append(
                row.event.pitch
                if isinstance(row.event, Note)
                else (0 if isinstance(row.event, GateOff) else hold)
            )
    return out + [end]


def _command(lo: int, hi: int) -> Command:
    kind, low = lo & 0xF0, lo & 0x0F
    if kind < 0x30:
        return Slide(bool(lo & 0x20), low << 8 | hi, lo & 0x10)
    if kind == 0x60:
        return Vibrato(low, hi >> 4, hi & 0x0F)
    for cls, code in _VALUE_COMMANDS.items():
        if kind == code:
            return cls(hi, low)
    return SetInstrumentWave(lo & 0x1F, hi, lo & 0xE0)


def command_bytes(cmd: Command) -> Tuple[int, int]:
    """The two bytes of a command-table entry."""
    if isinstance(cmd, Slide):
        lo = (0x20 if cmd.down else 0) | cmd.reserved | cmd.speed >> 8
        return lo, cmd.speed & 0xFF
    if isinstance(cmd, Vibrato):
        return 0x60 | cmd.speed, cmd.delay << 4 | cmd.depth
    if isinstance(cmd, SetInstrumentWave):
        return cmd.reserved | cmd.instrument, cmd.wave
    return _VALUE_COMMANDS[type(cmd)] | cmd.reserved, cmd.value


def _instrument(rec: List[int]) -> Instrument:
    return Instrument(
        attack_decay=rec[0],
        sustain_release=rec[1],
        hard_restart=bool(rec[2] & 0x80),
        absolute=bool(rec[2] & 0x40),
        wave_speed=rec[2] & 0x0F,
        filter_resonance=rec[3] >> 4,
        filter_mode=rec[3] & 0x0F,
        filter_program=rec[4],
        pulse_program=rec[5],
        wave=rec[6],
        wave_release=rec[7],
        reserved=rec[2] & 0x30,
    )


def instrument_bytes(ins: Instrument) -> List[int]:
    """The eight bytes of an instrument record."""
    flags = (0x80 if ins.hard_restart else 0) | (0x40 if ins.absolute else 0)
    return [
        ins.attack_decay,
        ins.sustain_release,
        flags | ins.reserved | ins.wave_speed,
        ins.filter_resonance << 4 | ins.filter_mode,
        ins.filter_program,
        ins.pulse_program,
        ins.wave,
        ins.wave_release,
    ]


def _wave_row(note: int, ctrl: int) -> WaveRow:
    if note == 0x7F:
        return WaveJump(ctrl)
    if note == 0x7E:
        return WaveHold(ctrl)
    return WaveStep(ctrl, note & 0x7F, bool(note & 0x80))


def wave_bytes(row: WaveRow) -> Tuple[int, int]:
    """A wave row's (note column, waveform column) bytes."""
    if isinstance(row, WaveJump):
        return 0x7F, row.row
    if isinstance(row, WaveHold):
        return 0x7E, row.wave
    return row.note | (0x80 if row.absolute else 0), row.wave


def _pulse(rec: List[int]) -> PulseStep:
    width = None if rec[0] == 0xFF else (rec[0] & 0xF0) | (rec[0] & 0x0F) << 8
    return PulseStep(width, rec[1], bool(rec[2] & 0x80), rec[2] & 0x7F, rec[3])


def pulse_bytes(step: PulseStep) -> List[int]:
    """A pulse record's four bytes."""
    init = 0xFF if step.width is None else (step.width & 0xF0) | step.width >> 8
    return [init, step.add, (0x80 if step.down else 0) | step.frames, step.next]


def _filter(rec: List[int]) -> FilterStep:
    return FilterStep(None if rec[0] == 0xFF else rec[0], rec[1], rec[2], rec[3])


def filter_bytes(step: FilterStep) -> List[int]:
    """A filter record's four bytes."""
    cutoff = 0xFF if step.cutoff is None else step.cutoff
    return [cutoff, step.add, step.frames, step.next]


# ---- the song data, as bytes ----------------------------------------------
@dataclass
class SongData:
    """A song's data as the player reads it, table by table (``None``: no table)."""

    # pylint: disable=too-many-instance-attributes  # one field per table
    tracks: List[List[int]] = field(default_factory=list)
    sequences: Dict[int, List[int]] = field(default_factory=dict)
    subtunes: List[List[int]] = field(default_factory=list)
    pitch: List[int] = field(default_factory=list)
    instruments: Optional[List[int]] = None
    commands: Optional[List[int]] = None
    wave: Optional[List[int]] = None
    pulse: Optional[List[int]] = None
    filter: Optional[List[int]] = None


def encode(song: JchSong) -> SongData:
    """The song data of a typed song, from its typed values alone."""
    d = song.dialect
    data = SongData(
        tracks=[track_bytes(d, t) for s in song.subtunes for t in s.tracks],
        sequences={i: sequence_bytes(d, s) for i, s in song.sequences.items()},
        subtunes=[
            [s.tempo] + ([] if s.flags is None else [s.flags]) for s in song.subtunes
        ],
        pitch=[b for w in song.pitch for b in (w & 0xFF, w >> 8)],
    )
    if d is Dialect.NEWPLAYER:
        data.instruments = [b for i in song.instruments for b in instrument_bytes(i)]
    if song.commands is not None:
        data.commands = [b for c in song.commands for b in command_bytes(c)]
    if song.wave is not None:
        cols = [wave_bytes(r) for r in song.wave]
        data.wave = [n for n, _ in cols] + [c for _, c in cols]
    if song.pulse is not None:
        data.pulse = [b for s in song.pulse for b in pulse_bytes(s)]
    if song.filter is not None:
        data.filter = [b for s in song.filter.steps for b in filter_bytes(s)]
    return data


# ---- reading a located model ----------------------------------------------
_WALK = 1024


@dataclass(frozen=True)
class _Tables:
    """Where a located model's tables start (``None``: the build has none)."""

    cmdparam: Optional[int]
    wave_note: Optional[int]
    wave_ctrl: Optional[int]
    pwprog: Optional[int]
    filterprog: Optional[int]
    pitch: Optional[int]
    notes: Optional[int]


class _Reader:
    """Byte runs and table extents over a located model."""

    def __init__(self, model):
        self.model = model
        self.v0x = isinstance(model, V0xLayout)
        self.dialect = Dialect.V0X if self.v0x else Dialect.NEWPLAYER
        if self.v0x:
            return
        b = discover_bases(model.load_addr, model.image, model)
        self.tables = (
            _Tables(
                b.cmdparam,
                b.wave_note,
                b.wave_ctrl,
                b.pwprog,
                b.filterprog,
                b.pitch,
                b.notes,
            )
            if b
            else _Tables(
                model.cmdparam,
                model.wave_note_col,
                model.wave_ctrl_col,
                model.pwprog,
                model.filterprog,
                model.pitch_table,
                None,
            )
        )

    def byte(self, addr: int) -> int:
        value = self.model._byte(addr)  # pylint: disable=protected-access
        if value is None:
            raise SidParseError(f"${addr:04X} outside the image")
        return value

    def run(self, addr: int, ends) -> List[int]:
        """Bytes from ``addr`` through the first one ``ends`` accepts."""
        out = []
        for i in range(_WALK):
            out.append(self.byte(addr + i))
            if ends(out[-1]):
                return out
        raise SidParseError(f"no terminator after ${addr:04X}")

    def block(self, addr: int, length: int) -> List[int]:
        return [self.byte(addr + i) for i in range(length)]

    def track_end(self, byte: int) -> bool:
        return byte >= 0xA0 if self.v0x else byte in (0xFE, 0xFF)

    def sequence_end(self, byte: int) -> bool:
        return byte >= 0x90 if self.v0x else byte == 0x7F


def _closure(starts, link) -> List[int]:
    seen, work = set(), list(starts)
    while work:
        k = work.pop()
        if k not in seen:
            seen.add(k)
            work.extend(link(k))
    return sorted(seen)


def _record_count(reader: _Reader, base: int, starts) -> int:
    """Records a 4-byte program reaches from ``starts`` through its ``next`` links.

    Links are byte offsets; one that is not a record boundary is data no record
    starts at, so it is kept in its record and not followed.
    """

    def link(k):
        nxt = reader.byte(base + k + 3)
        return [] if nxt % 4 else [nxt]

    return max(_closure([k for k in starts if k % 4 == 0] or [0], link)) // 4 + 1


def _wave_rows(reader: _Reader, tables: _Tables, starts) -> int:
    span = tables.wave_ctrl - tables.wave_note
    if 0 < span <= 0x100:  # two parallel columns, back to back
        return span

    def link(j):
        if reader.byte(tables.wave_note + j) == 0x7F:
            return [reader.byte(tables.wave_ctrl + j)]
        return [j + 1] if j < 0xFF else []

    return max(_closure(starts, link)) + 1


def _pitch_words(reader: _Reader, tables: _Tables) -> int:
    if tables.notes is not None:
        return tables.notes
    model = reader.model
    others = (
        model.subtune_table,
        model.patternptr_lo,
        model.patternptr_hi,
        model.instruments,
        tables.wave_note,
        tables.wave_ctrl,
        tables.cmdparam,
        tables.filterprog,
        tables.pwprog,
    )
    end = min(
        [b for b in others if b is not None and b > tables.pitch],
        default=model.load_addr + len(model.image),
    )
    return min(end - tables.pitch, 0x100) // 2


def decode(model) -> Tuple[JchSong, SongData]:
    """The typed song of a located model, and the original bytes it was read from."""
    reader = _Reader(model)
    data = SongData()
    subtunes = []
    for sub in range(1 if reader.v0x else model.num_subtunes):
        tracks = []
        for voice in range(model.voices):
            raw = reader.run(model.orderlist_ptr(sub, voice), reader.track_end)
            data.tracks.append(raw)
            tracks.append(_track(reader.dialect, raw))
        subtunes.append(_subtune(reader, sub, tuple(tracks), data))
    sequences = {}
    for index in sorted(
        {
            op.sequence
            for s in subtunes
            for t in s.tracks
            for op in t.ops
            if isinstance(op, PlaySequence)
        }
    ):
        addr = model.subpattern_ptr(index) if reader.v0x else model.pattern_ptr(index)
        data.sequences[index] = reader.run(addr, reader.sequence_end)
        sequences[index] = _sequence(reader.dialect, data.sequences[index])
    if reader.v0x:
        data.pitch = reader.block(model.load_addr + 0x21F, 0x80)
        song = JchSong(
            reader.dialect,
            tuple(subtunes),
            sequences,
            (),
            None,
            None,
            None,
            None,
            _words(data.pitch),
        )
        return song, data
    return _tables(reader, tuple(subtunes), sequences, data), data


def _words(raw: List[int]) -> Tuple[int, ...]:
    return tuple(raw[i] | raw[i + 1] << 8 for i in range(0, len(raw) - 1, 2))


def _subtune(reader: _Reader, sub: int, tracks, data) -> Subtune:
    model = reader.model
    if reader.v0x:
        raw = [reader.byte(model.orderlist_ptr_table + 6)]
    else:
        base = model.subtune_table + sub * model.record + model.tempo_offset
        raw = reader.block(base, 2 if model.tempo_offset + 1 < model.record else 1)
    data.subtunes.append(raw)
    return Subtune(raw[0], raw[1] if len(raw) > 1 else None, tracks)


def _tables(reader: _Reader, subtunes, sequences, data) -> JchSong:
    tables, model = reader.tables, reader.model
    prefixes = [p for s in sequences.values() for r in s.rows for p in r.prefix]
    commands = None
    if tables.cmdparam is not None:
        used = [p.command for p in prefixes if isinstance(p, RunCommand)]
        data.commands = reader.block(tables.cmdparam, 2 * (max(used, default=-1) + 1))
        commands = tuple(
            _command(data.commands[i], data.commands[i + 1])
            for i in range(0, len(data.commands), 2)
        )
    used = [p.instrument for p in prefixes if isinstance(p, SelectInstrument)]
    used += [c.instrument for c in commands or () if isinstance(c, SetInstrumentWave)]
    data.instruments = reader.block(model.instruments, 8 * (max(used, default=0) + 1))
    instruments = tuple(
        _instrument(data.instruments[i : i + 8])
        for i in range(0, len(data.instruments), 8)
    )
    wave = pulse = filt = None
    if tables.wave_note is not None and tables.wave_ctrl is not None:
        starts = [i.wave for i in instruments] + [i.wave_release for i in instruments]
        starts += [c.wave for c in commands or () if isinstance(c, SetInstrumentWave)]
        rows = _wave_rows(reader, tables, starts)
        data.wave = reader.block(tables.wave_note, rows) + reader.block(
            tables.wave_ctrl, rows
        )
        wave = tuple(_wave_row(data.wave[j], data.wave[rows + j]) for j in range(rows))
    if tables.pwprog is not None:
        count = _record_count(
            reader, tables.pwprog, [i.pulse_program for i in instruments]
        )
        data.pulse = reader.block(tables.pwprog, 4 * count)
        pulse = tuple(_pulse(data.pulse[k : k + 4]) for k in range(0, 4 * count, 4))
    if tables.filterprog is not None:
        starts = [0] + [i.filter_program for i in instruments]
        count = _record_count(reader, tables.filterprog, starts)
        data.filter = reader.block(tables.filterprog, 4 * count)
        filt = FilterTable(
            tuple(_filter(data.filter[k : k + 4]) for k in range(0, 4 * count, 4))
        )
    if tables.pitch is not None:
        data.pitch = reader.block(tables.pitch, 2 * _pitch_words(reader, tables))
    return JchSong(
        reader.dialect,
        subtunes,
        sequences,
        instruments,
        commands,
        wave,
        pulse,
        filt,
        _words(data.pitch),
    )
