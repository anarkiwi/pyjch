"""Recover a neutral :class:`~pyjch.songmodel.Tune` from a parsed model.

:func:`extract` walks the tables :mod:`pyjch.newplayer` located (and, when the
tune is the V20 code build, the extra tables :func:`pyjch.player.v20_bases`
resolves) and decodes them with exactly the semantics the V20 player applies at
runtime -- statically, without re-executing the player.  Decode rules mirrored
from the V20 routine in :mod:`pyjch.player`:

* order lists: ``$FE`` stop / ``$FF`` loop / ``$80+`` transpose prefix;
* patterns: walk to ``$7F``; ``$80|$0F`` duration (+``$10`` delay), ``$A0|$1F``
  instrument select, ``$C0+`` command via the command-parameter table;
* instruments: the 8-byte record (AD, SR, wave-flags, filter, filter-prog,
  PW-start, wave-start, wave-start2);
* pulse/filter programs: 4-byte records; filter idx 0-1 doubles as the groove.

Block tables are bounded by the next-higher discovered base (clamped to the
image end); instruments/commands by the max referenced index.  A table that
will not decode coherently is left absent and noted in
:class:`~pyjch.songmodel.Provenance`, never emitted as garbage.  A V0x
:class:`~pyjch.model.Song` is out of scope and raises ``SidParseError``.
"""

from dataclasses import asdict, astuple, dataclass
from typing import Any, Dict, List, Optional, Set

from pyjch import grammar, player
from pyjch.errors import SidParseError
from pyjch.model import Song
from pyjch.newplayer import NewPlayerModel
from pyjch.player import V20Bases
from pyjch.variant import detect_variant
from pyjch.songmodel import (
    Command,
    CommandEvent,
    CommandKind,
    Instrument,
    NoteEvent,
    NoteKind,
    OrderEntry,
    OrderList,
    PatternEvent,
    Provenance,
    PwStep,
    FilterStep,
    Subtune,
    Tune,
    WaveTable,
)


def _stream(model, addr: Optional[int], rule: str, what: str, reach: int):
    """The ``rule`` stream at ``addr`` (a :class:`SidParseError` if malformed)."""
    if addr is None:
        raise SidParseError(f"{what}: pointer out of range")
    size = min(reach, model.load_addr + len(model.image) - addr)
    return grammar.read_stream(model.sid, addr, rule, what, size)


_COMMAND_HI = {
    0x60: CommandKind.VIBRATO,
    0x90: CommandKind.AD_OVERRIDE,
    0xE0: CommandKind.TEMPO,
    0xF0: CommandKind.VOLUME,
}


def _command_kind(param_lo: int) -> CommandKind:
    """Classify a command-table entry by its lo byte's high nibble."""
    high = param_lo & 0xF0
    if high < 0x30:
        return CommandKind.PORTAMENTO
    return _COMMAND_HI.get(high, CommandKind.WAVE_PATCH)


def _decode_note(byte: int) -> NoteEvent:
    if byte == 0:
        return NoteEvent(NoteKind.REST, 0)
    if byte == 0x7E:
        return NoteEvent(NoteKind.HOLD, byte)
    return NoteEvent(NoteKind.NOTE, byte)


def _decode_command(model, byte: int, cmdparam: Optional[int]) -> CommandEvent:
    hi = byte & 0xE0
    if hi == 0x80:
        return CommandEvent(
            CommandKind.DURATION, byte, arg=byte & 0x0F, delay=bool(byte & 0x10)
        )
    if hi == 0xA0:
        return CommandEvent(CommandKind.INSTRUMENT, byte, arg=byte & 0x1F)
    index = byte & 0x3F
    if cmdparam is None:
        return CommandEvent(None, byte, cmd_index=index)
    lo = model._byte(cmdparam + index * 2)  # pylint: disable=protected-access
    hi_b = model._byte(cmdparam + index * 2 + 1)  # pylint: disable=protected-access
    if lo is None or hi_b is None:
        return CommandEvent(None, byte, cmd_index=index)
    return CommandEvent(
        _command_kind(lo), byte, cmd_index=index, param_lo=lo, param_hi=hi_b
    )


def _decode_orderlist(model, subtune: int, voice: int) -> OrderList:
    addr = model.orderlist_ptr(subtune, voice)
    stream = _stream(
        model,
        addr,
        grammar.ORDERLIST,
        f"order list {subtune}.{voice}",
        grammar.ORDERLIST_REACH,
    )
    entries = [OrderEntry(pattern, transpose) for pattern, transpose in stream.steps]
    return OrderList(
        entries=entries, loops=stream.loops, restart=0, raw=list(stream.raw)
    )


def _decode_pattern(model, base: Optional[int], cmdparam: Optional[int], index: int):
    """The decoded events and source bytes of pattern ``index`` at ``base``."""
    if base is None:
        return [], []
    stream = _stream(
        model,
        base,
        grammar.pattern_rule(model),
        f"pattern {index}",
        grammar.PATTERN_REACH,
    )
    events = []
    for byte in stream.events:
        if byte >= 0x80:
            events.append(
                PatternEvent(byte, command=_decode_command(model, byte, cmdparam))
            )
        else:
            events.append(PatternEvent(byte, note=_decode_note(byte)))
    return events, list(stream.raw)


def _decode_instrument(rec: List[int]) -> Instrument:
    rec = list(grammar.GRAMMAR.parse_exact(bytes(rec), grammar.INSTRUMENT).raw)
    return Instrument(
        raw=rec,
        ad=rec[0],
        sr=rec[1],
        wave_speed=rec[2] & 0x0F,
        abs_freq=bool(rec[2] & 0x40),
        flag7=bool(rec[2] & 0x80),
        filter_mode=rec[3] & 0xF0,
        filter_route=rec[3] & 0x0F,
        filter_prog=rec[4],
        pw_prog=rec[5],
        wave_start=rec[6],
        wave_start2=rec[7],
    )


def _next_base(bases: List[int], base: int, image_end: int) -> int:
    """Smallest discovered base above ``base`` (clamped to the image end)."""
    higher = [b for b in bases if b > base]
    return min(higher) if higher else image_end


def _slice(model, base: int, end: int) -> List[int]:
    out = []
    for addr in range(base, end):
        val = model._byte(addr)  # pylint: disable=protected-access
        out.append(0 if val is None else val)
    return out


def _records(raw: List[int], cls):
    """4-byte records over ``raw``, trailing all-zero records trimmed (min 1)."""
    count = len(raw) // 4
    recs = [
        cls(
            *grammar.GRAMMAR.parse_exact(
                bytes(raw[i * 4 : i * 4 + 4]), grammar.STEP
            ).raw
        )
        for i in range(count)
    ]
    zero = cls(0, 0, 0, 0)
    while len(recs) > 1 and recs[-1] == zero:
        recs.pop()
    return recs


def _all_bases(model, fixed: List[Optional[int]], pat_idx: Set[int]) -> List[int]:
    bases = {b for b in fixed if b is not None}
    for subtune in range(model.num_subtunes):
        for voice in range(3):
            ptr = model.orderlist_ptr(subtune, voice)
            if ptr is not None:
                bases.add(ptr)
    for idx in pat_idx:
        ptr = model.pattern_ptr(idx)
        if ptr is not None:
            bases.add(ptr)
    return sorted(bases)


def _collect(model, cmdparam: Optional[int]):
    """Order lists, patterns, and the referenced index sets."""
    subtunes: List[Subtune] = []
    pat_idx: Set[int] = set()
    for subtune in range(model.num_subtunes):
        lists = [_decode_orderlist(model, subtune, v) for v in range(3)]
        for order in lists:
            pat_idx.update(e.pattern for e in order.entries)
        subtunes.append(Subtune(lists, tempo=model.subtune_tempo(subtune) or 0))
    max_pat = max(pat_idx) if pat_idx else -1
    patterns: List[List[PatternEvent]] = []
    pattern_raw: List[List[int]] = []
    inst_ref: Set[int] = {0}
    cmd_ref: Set[int] = set()
    for idx in range(max_pat + 1):
        events, raw = _decode_pattern(model, model.pattern_ptr(idx), cmdparam, idx)
        patterns.append(events)
        pattern_raw.append(raw)
        for event in events:
            cmd = event.command
            if cmd is None:
                continue
            if cmd.kind is CommandKind.INSTRUMENT and cmd.arg is not None:
                inst_ref.add(cmd.arg)
            if cmd.cmd_index is not None:
                cmd_ref.add(cmd.cmd_index)
    return subtunes, patterns, pattern_raw, inst_ref, cmd_ref


def _instruments(model, base: int, image_end: int, all_bases, inst_ref) -> List[int]:
    # The pattern instrument-select operand is 5 bits, so at most 32 records
    # are addressable; clamp the region so a distant next-base never over-reads.
    region = min((_next_base(all_bases, base, image_end) - base) // 8, 32)
    last = 0
    for i in range(region):
        rec = model.instrument(i)
        if rec is not None and any(rec):
            last = i
    count = min(region, max(last, max(inst_ref)) + 1)
    out = []
    for i in range(count):
        rec = model.instrument(i)
        if rec is None:
            break
        out.append(_decode_instrument(rec))
    return out


_TABLE_MAX = 256  # wave/pitch tables are byte-indexed -> at most 256 entries


def _wave_slice(model, base: int, cap: int) -> List[int]:
    return _slice(model, base, min(cap, base + _TABLE_MAX))


def _variant_info(model) -> Dict[str, Any]:
    """The detected variant of ``model``: facet values plus ``<facet>.src``."""
    return detect_variant(model).as_info()


@dataclass(frozen=True)
class _Tables:
    """The optional data-table bases a tune's decode reads (``None`` = absent)."""

    # pylint: disable=too-many-instance-attributes  # one field per table
    wave_note: Optional[int]
    wave_ctrl: Optional[int]
    pitch: Optional[int]
    cmdparam: Optional[int]
    filterprog: Optional[int]
    pwprog: Optional[int]


def _tables(model, bases: Optional[V20Bases]) -> _Tables:
    """The V20 build's resolved bases, else the family model's discovered ones."""
    if bases is not None:
        return _Tables(
            bases.wave_note,
            bases.wave_ctrl,
            bases.pitch,
            bases.cmdparam,
            bases.filterprog,
            bases.pwprog,
        )
    return _Tables(
        model.wave_note_col,
        model.wave_ctrl_col,
        model.pitch_table,
        model.cmdparam,
        model.filterprog,
        model.pwprog,
    )


def _commands(model, cmdparam: int, cmd_ref: Set[int]) -> List[Command]:
    if not cmd_ref:
        return []
    out = []
    for i in range(max(cmd_ref) + 1):
        lo = model._byte(cmdparam + i * 2)  # pylint: disable=protected-access
        hi = model._byte(cmdparam + i * 2 + 1)  # pylint: disable=protected-access
        if lo is None or hi is None:
            break
        out.append(Command(lo, hi, _command_kind(lo)))
    return out


def _wavetable(model, tables: _Tables, all_bases, end: int, notes: List[str]):
    """The wave table the build lays out, noting what is not recovered."""
    if tables.wave_note is not None and tables.wave_ctrl is not None:
        note = _wave_slice(
            model, tables.wave_note, _next_base(all_bases, tables.wave_note, end)
        )
        ctrl = _wave_slice(
            model, tables.wave_ctrl, _next_base(all_bases, tables.wave_ctrl, end)
        )
        return WaveTable(note, ctrl)
    if tables.wave_note is not None:
        note_end = _next_base(all_bases, tables.wave_note, end)
        notes.append("wave ctrl column not recovered (note column only)")
        return WaveTable(_wave_slice(model, tables.wave_note, note_end), [])
    if model.wave_stream is not None:
        notes.append(
            "wavetable is the V1/V2 single interleaved (ctrl,note) stream at "
            f"${model.wave_stream:04x}; its $FF-restart loop target is the "
            "instrument's own wave-start field, so the stream de-interleaves to "
            "the editor's two columns -- but a faithful editor export is still "
            "blocked by the ~16-byte instrument records and the embedded "
            "limit-based PW/filter, which have no editor representation"
        )
    else:
        notes.append("wavetable not recovered")
    return None


def _family_provenance(model, tables: _Tables, notes: List[str]) -> Provenance:
    bases = {
        "subtune_table": model.subtune_table,
        "patternptr_lo": model.patternptr_lo,
        "patternptr_hi": model.patternptr_hi,
        "instruments": model.instruments,
    }
    for key, val in (
        ("wave_note_col", tables.wave_note),
        ("wave_ctrl_col", tables.wave_ctrl),
        ("pitch_table", tables.pitch),
        ("cmdparam", tables.cmdparam),
        ("filterprog", tables.filterprog),
        ("pwprog", tables.pwprog),
    ):
        if val is not None:
            bases[key] = val
    return Provenance(
        "family", model.version, bases, notes, variant=_variant_info(model)
    )


def _extract_tune(model, bases: Optional[V20Bases]) -> Tune:
    """Decode ``model``: the full V20 build when ``bases`` resolved, else the family subset."""
    load = model.load_addr
    end = load + len(model.image)
    tables = _tables(model, bases)
    subtunes, patterns, pattern_raw, inst_ref, cmd_ref = _collect(
        model, tables.cmdparam
    )
    all_bases = _all_bases(
        model,
        [
            model.subtune_table,
            model.patternptr_lo,
            model.patternptr_hi,
            model.instruments,
            *astuple(tables),
        ],
        set(range(len(patterns))),
    )
    notes: List[str] = []
    wavetable = _wavetable(model, tables, all_bases, end, notes)
    pitch: List[int] = []
    if tables.pitch is not None:
        pitch = _wave_slice(
            model, tables.pitch, _next_base(all_bases, tables.pitch, end)
        )
    else:
        notes.append("pitch table not recovered")
    filt: List[int] = []
    if tables.filterprog is not None:
        filt = _slice(
            model, tables.filterprog, _next_base(all_bases, tables.filterprog, end)
        )
    pw: List[int] = []
    if tables.pwprog is not None:
        pw = _slice(model, tables.pwprog, _next_base(all_bases, tables.pwprog, end))
    commands: List[Command] = []
    if tables.cmdparam is not None:
        commands = _commands(model, tables.cmdparam, cmd_ref)
    else:
        notes.append("$C0 pattern commands unclassified (no command table)")
    if tables.filterprog is None or tables.pwprog is None:
        notes.append(
            "pulse/filter program not recovered: the classic-family (V6-V18) sweep "
            "is a limit-based ping-pong (4-byte entry = packed up/down limits, "
            "step, flags+dwell, value; sequential Y+=4 cursor) that auto-reflects "
            "at the packed hi/lo limits -- the NP20-25 editor's next-index-chained "
            "(value, step, dwell, next) format has no limits column, so the sweep "
            "is not faithfully representable (not a mere column reorder)"
        )
    if bases is None:
        notes.append("hard-restart not recovered")
        provenance = _family_provenance(model, tables, notes)
    else:
        provenance = Provenance(
            "v20", model.version, asdict(bases), variant=_variant_info(model)
        )
    return Tune(
        name=model.name,
        author=model.author,
        released=model.released,
        load_addr=load,
        init_addr=model.init_addr,
        play_addr=model.play_addr,
        hard_restart=None if bases is None else bases.hardrestart,
        subtunes=subtunes,
        patterns=patterns,
        pattern_raw=pattern_raw,
        instruments=_instruments(model, model.instruments, end, all_bases, inst_ref),
        wavetable=wavetable,
        pw_program=_records(pw, PwStep),
        filter_program=_records(filt, FilterStep),
        groove=filt[:2],
        pitch_table=pitch,
        commands=commands,
        provenance=provenance,
    )


def extract(model) -> Tune:
    """Extract a neutral :class:`~pyjch.songmodel.Tune` from a parsed model.

    Full V20 decode when the tune is the V20 code build
    (:func:`pyjch.player.playable`), else the family subset with absent
    tables noted.  Raises :class:`~pyjch.errors.SidParseError` for a V0x
    :class:`~pyjch.model.Song` (out of scope for this wavetable-family export).
    """
    if isinstance(model, Song):
        raise SidParseError(
            "extract targets the JCH NewPlayer wavetable family; the V0x Song "
            "layout is out of scope"
        )
    if not isinstance(model, NewPlayerModel):
        raise SidParseError(f"cannot extract a Tune from {type(model).__name__}")
    return _extract_tune(model, player.playable(model))
