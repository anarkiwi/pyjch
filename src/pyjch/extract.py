"""Recover a neutral :class:`~pyjch.songmodel.Tune` from a parsed model.

:func:`extract` walks the tables :mod:`pyjch.newplayer` located (and, when the
tune is the V20 code build, the extra tables :mod:`pyjch.v20player` resolves)
and decodes them with exactly the semantics the V20 player applies at runtime --
statically, without re-executing the player.  Decode rules mirrored from
``pyjch.v20player``:

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

from dataclasses import asdict
from typing import List, Optional, Set

from pyjch import v20player
from pyjch.errors import SidParseError
from pyjch.model import Song
from pyjch.newplayer import NewPlayerModel
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

_MAX_WALK = 512  # order-list length cap for the static walk
_MAX_PATTERN = 1024  # pattern length cap (longest real sequence observed: 820 B)


def _raw_stream(
    model, addr: Optional[int], terminators, limit: int = _MAX_WALK
) -> List[int]:
    """Source bytes from ``addr`` through (and including) the first terminator.

    A verbatim slice for faithful re-emit; needs only the base + terminators, so
    it works for both the V20 tier and the family tier (no full decode). Empty
    when ``addr`` is absent.
    """
    if addr is None:
        return []
    out: List[int] = []
    for _ in range(limit):
        byte = model._byte(addr)  # pylint: disable=protected-access
        if byte is None:
            break
        out.append(byte)
        addr += 1
        if byte in terminators:
            break
    return out


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
    entries: List[OrderEntry] = []
    loops = False
    transpose = 0
    for _ in range(_MAX_WALK):
        byte = model._byte(addr)  # pylint: disable=protected-access
        if byte is None:
            break
        addr += 1
        if byte == 0xFE:
            break
        if byte == 0xFF:
            loops = True
            break
        if byte >= 0x80:
            transpose = byte
            continue
        entries.append(OrderEntry(pattern=byte, transpose=transpose))
        transpose = 0
    raw = _raw_stream(model, model.orderlist_ptr(subtune, voice), (0xFE, 0xFF))
    return OrderList(entries=entries, loops=loops, restart=0, raw=raw)


def _decode_pattern(model, base: Optional[int], cmdparam: Optional[int]):
    events: List[PatternEvent] = []
    if base is None:
        return events
    addr = base
    for _ in range(_MAX_PATTERN):
        byte = model._byte(addr)  # pylint: disable=protected-access
        if byte is None or byte == 0x7F:
            break
        addr += 1
        if byte >= 0x80:
            events.append(
                PatternEvent(byte, command=_decode_command(model, byte, cmdparam))
            )
        else:
            events.append(PatternEvent(byte, note=_decode_note(byte)))
    return events


def _decode_instrument(rec: List[int]) -> Instrument:
    return Instrument(
        raw=list(rec),
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
    recs = [cls(*raw[i * 4 : i * 4 + 4]) for i in range(count)]
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
        base = model.pattern_ptr(idx)
        events = _decode_pattern(model, base, cmdparam)
        patterns.append(events)
        pattern_raw.append(_raw_stream(model, base, (0x7F,), _MAX_PATTERN))
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


def _extract_v20(model, bases) -> Tune:
    load = model.load_addr
    end = load + len(model.image)
    subtunes, patterns, pattern_raw, inst_ref, cmd_ref = _collect(model, bases.cmdparam)
    fixed = [
        bases.pitch,
        bases.subtune,
        bases.wave_note,
        bases.wave_ctrl,
        bases.filterprog,
        bases.pwprog,
        bases.instruments,
        bases.patternptr_lo,
        bases.patternptr_hi,
        bases.cmdparam,
    ]
    all_bases = _all_bases(model, fixed, set(range(len(patterns))))
    note_end = _next_base(all_bases, bases.wave_note, end)
    ctrl_end = _next_base(all_bases, bases.wave_ctrl, end)
    wavetable = WaveTable(
        _wave_slice(model, bases.wave_note, note_end),
        _wave_slice(model, bases.wave_ctrl, ctrl_end),
    )
    filt = _slice(model, bases.filterprog, _next_base(all_bases, bases.filterprog, end))
    pw = _slice(model, bases.pwprog, _next_base(all_bases, bases.pwprog, end))
    pitch = _wave_slice(model, bases.pitch, _next_base(all_bases, bases.pitch, end))
    commands = _commands(model, bases.cmdparam, cmd_ref)
    instruments = _instruments(model, bases.instruments, end, all_bases, inst_ref)
    return Tune(
        name=model.name,
        author=model.author,
        released=model.released,
        load_addr=load,
        init_addr=model.init_addr,
        play_addr=model.play_addr,
        hard_restart=bases.hardrestart,
        subtunes=subtunes,
        patterns=patterns,
        pattern_raw=pattern_raw,
        instruments=instruments,
        wavetable=wavetable,
        pw_program=_records(pw, PwStep),
        filter_program=_records(filt, FilterStep),
        groove=filt[:2],
        pitch_table=pitch,
        commands=commands,
        provenance=Provenance("v20", model.version, asdict(bases)),
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


def _extract_family(model) -> Tune:  # pylint: disable=too-many-locals
    load = model.load_addr
    end = load + len(model.image)
    subtunes, patterns, pattern_raw, inst_ref, cmd_ref = _collect(model, model.cmdparam)
    fixed = [
        model.subtune_table,
        model.patternptr_lo,
        model.patternptr_hi,
        model.instruments,
        model.wave_note_col,
        model.wave_ctrl_col,
        model.pitch_table,
        model.cmdparam,
        model.filterprog,
        model.pwprog,
    ]
    all_bases = _all_bases(model, fixed, set(range(len(patterns))))
    instruments = _instruments(model, model.instruments, end, all_bases, inst_ref)
    notes: List[str] = []
    wavetable = None
    if model.wave_note_col is not None and model.wave_ctrl_col is not None:
        note = _wave_slice(
            model, model.wave_note_col, _next_base(all_bases, model.wave_note_col, end)
        )
        ctrl = _wave_slice(
            model, model.wave_ctrl_col, _next_base(all_bases, model.wave_ctrl_col, end)
        )
        wavetable = WaveTable(note, ctrl)
    elif model.wave_note_col is not None:
        note_end = _next_base(all_bases, model.wave_note_col, end)
        wavetable = WaveTable(_wave_slice(model, model.wave_note_col, note_end), [])
        notes.append("wave ctrl column not recovered (note column only)")
    else:
        notes.append("wavetable not recovered")
    pitch: List[int] = []
    if model.pitch_table is not None:
        pitch = _wave_slice(
            model, model.pitch_table, _next_base(all_bases, model.pitch_table, end)
        )
    else:
        notes.append("pitch table not recovered")
    filt: List[int] = []
    if model.filterprog is not None:
        filt = _slice(
            model, model.filterprog, _next_base(all_bases, model.filterprog, end)
        )
    pw: List[int] = []
    if model.pwprog is not None:
        pw = _slice(model, model.pwprog, _next_base(all_bases, model.pwprog, end))
    commands = (
        _commands(model, model.cmdparam, cmd_ref) if model.cmdparam is not None else []
    )
    if model.cmdparam is None:
        notes.append("$C0 pattern commands unclassified (no command table)")
    if model.filterprog is None or model.pwprog is None:
        notes.append(
            "pulse/filter program not recovered (classic-family sweep uses a "
            "sequential Y+=4 cursor with no next-index column and a different "
            "column order than the NP20-25 editor pulse/filter format)"
        )
    notes.append("hard-restart not recovered")
    bases = {
        "subtune_table": model.subtune_table,
        "patternptr_lo": model.patternptr_lo,
        "patternptr_hi": model.patternptr_hi,
        "instruments": model.instruments,
    }
    for key in ("wave_note_col", "wave_ctrl_col", "pitch_table", "cmdparam"):
        val = getattr(model, key)
        if val is not None:
            bases[key] = val
    return Tune(
        name=model.name,
        author=model.author,
        released=model.released,
        load_addr=load,
        init_addr=model.init_addr,
        play_addr=model.play_addr,
        hard_restart=None,
        subtunes=subtunes,
        patterns=patterns,
        pattern_raw=pattern_raw,
        instruments=instruments,
        wavetable=wavetable,
        pw_program=_records(pw, PwStep),
        filter_program=_records(filt, FilterStep),
        groove=filt[:2],
        pitch_table=pitch,
        commands=commands,
        provenance=Provenance("family", model.version, bases, notes),
    )


def extract(model) -> Tune:
    """Extract a neutral :class:`~pyjch.songmodel.Tune` from a parsed model.

    Full V20 decode when the tune is the V20 code build
    (:func:`pyjch.v20player.playable`), else the family subset with absent
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
    bases = v20player.playable(model)
    if bases is not None:
        return _extract_v20(model, bases)
    return _extract_family(model)
