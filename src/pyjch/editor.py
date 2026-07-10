"""Write a recovered :class:`~pyjch.songmodel.Tune` as an editor-native ``.prg``.

Implements the write-side spec in ``docs/editor-format.md``: inject a
caller-supplied stock player-code prefix (``driver`` -- never embedded here, per
the no-copyrighted-material rule), lay the recovered tables at the canonical
NP20 data-region bases, encode them, write the ``$0Fxx`` header pointer block +
version magic + default tempo, and emit a 2-byte-load ``.prg`` at ``$0F00``.

This is the **Tier-3 scaffold**.  The data-region + pointer-block assembly is
implemented for the parameters verified for NP20/20.G4 (:data:`NP20_PROFILE`).
The parameters that need the NP22-25 release ``.d64`` -- the native instrument
record width and the pitch/fine-tune table bytes -- are held on
:class:`Np20Profile`; a profile that leaves them unresolved raises
:class:`NotImplementedError` rather than guessing.  Order lists are laid in the
recovered packed encoding (``$80+`` transpose prefix, ``$FE``/``$FF`` terminator):
the editor's ``$20``-baselined pair re-encoding is the closed-source packer's
transform and is deferred with the other NP22-25 gaps.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from pyjch.errors import SidParseError
from pyjch.songmodel import OrderList, Tune

_MAX_INSTRUMENTS = 32
_MAX_SUBTUNES = 31
_MAX_PATTERNS = 114
_MAX_ROWS = 96

_PATTERN_END = 0x7F
_ORDER_LOOP = 0xFF
_ORDER_STOP = 0xFE


@dataclass
class Np20Profile:
    """Verified NP20/20.G4 write parameters; ``[P]`` fields gate NP22-25 gaps.

    ``instrument_record_width`` and ``pitch_resolved`` are the two parameters the
    NP22-25 ``.d64`` must confirm; leaving them unresolved makes the writer raise
    :class:`NotImplementedError` at the point it would need them.
    """

    version_magic: bytes = b"20.G"
    load_addr: int = 0x0F00
    init_addr: int = 0x1000
    play_addr: int = 0x1003
    # Header pointer-block word addresses (see docs/editor-format.md).
    ptr_init: int = 0x0FA6
    ptr_pitch: int = 0x0FBA
    ptr_wave: int = 0x0FBC
    ptr_filter: int = 0x0FC0
    ptr_pulse: int = 0x0FC2
    ptr_inst: int = 0x0FC4
    ptr_order: Tuple[int, int, int] = (0x0FC6, 0x0FC8, 0x0FCA)
    ptr_seqlo: int = 0x0FCC
    ptr_seqhi: int = 0x0FCE
    ptr_cmd: int = 0x0FD0
    ptr_magic: int = 0x0FEE
    # Canonical data-region bases.
    base_pitch: int = 0x17CB
    base_wave_note: int = 0x18CB
    base_wave_ctrl: int = 0x19CB
    base_filter: int = 0x1ACB
    base_pulse: int = 0x1BCB
    base_inst: int = 0x1CCB
    base_seqlo: int = 0x1DCB
    base_seqhi: int = 0x1ECB
    base_cmd: int = 0x1FCB
    base_order: Tuple[int, int, int] = (0x20CB, 0x24CB, 0x28CB)
    base_seq: int = 0x2CCB
    # Init-data base whose +6 byte the driver reads as the default tempo.
    base_init_data: int = 0x0F00
    # [P] NP22-25 gaps.
    instrument_record_width: Optional[int] = 8
    pitch_resolved: bool = True


NP20_PROFILE = Np20Profile()


def _encode_order(order: OrderList) -> List[int]:
    """Recovered packed order stream: transpose prefixes + ``$FE``/``$FF`` end."""
    out: List[int] = []
    current = 0
    for entry in order.entries:
        if entry.transpose and entry.transpose != current:
            out.append(entry.transpose)
            current = entry.transpose
        out.append(entry.pattern)
    out.append(_ORDER_LOOP if order.loops else _ORDER_STOP)
    return out


def _check_capacity(tune: Tune) -> None:
    if len(tune.instruments) > _MAX_INSTRUMENTS:
        raise SidParseError(f"too many instruments ({len(tune.instruments)} > 32)")
    if len(tune.subtunes) > _MAX_SUBTUNES:
        raise SidParseError(f"too many subtunes ({len(tune.subtunes)} > 31)")
    if len(tune.patterns) > _MAX_PATTERNS:
        raise SidParseError(f"too many patterns ({len(tune.patterns)} > 114)")
    for index, events in enumerate(tune.patterns):
        if len(events) > _MAX_ROWS:
            raise SidParseError(f"pattern {index} too long ({len(events)} > 96 rows)")


def _lay_instruments(tune: Tune, profile: Np20Profile, poke) -> None:
    width = profile.instrument_record_width
    if width is None:
        raise NotImplementedError(
            "native instrument record width is unresolved for this profile; "
            "confirm it from the NP22-25 .d64 before exporting instruments"
        )
    for index, inst in enumerate(tune.instruments):
        poke(profile.base_inst + index * width, inst.raw[:width])


def _lay_pitch(tune: Tune, profile: Np20Profile, poke) -> None:
    if not profile.pitch_resolved:
        raise NotImplementedError(
            "pitch / fine-tune table bytes are unresolved for this profile; "
            "read them from the NP22-25 .d64 before exporting the pitch table"
        )
    poke(profile.base_pitch, tune.pitch_table)


def _lay_sequences(tune: Tune, profile: Np20Profile, poke) -> None:
    addr = profile.base_seq
    for index, events in enumerate(tune.patterns):
        poke(profile.base_seqlo + index, [addr & 0xFF])
        poke(profile.base_seqhi + index, [(addr >> 8) & 0xFF])
        data = [event.raw for event in events] + [_PATTERN_END]
        poke(addr, data)
        addr += len(data)


def _lay_tables(tune: Tune, profile: Np20Profile, poke) -> None:
    if tune.wavetable is not None:
        poke(profile.base_wave_note, tune.wavetable.note_col)
        poke(profile.base_wave_ctrl, tune.wavetable.ctrl_col)
    filt: List[int] = []
    for step in tune.filter_program:
        filt += [step.value, step.step, step.dwell, step.next_off]
    poke(profile.base_filter, filt)
    pulse: List[int] = []
    for step in tune.pw_program:
        pulse += [step.reset, step.step, step.dir_rate, step.next_off]
    poke(profile.base_pulse, pulse)
    cmds: List[int] = []
    for cmd in tune.commands:
        cmds += [cmd.lo, cmd.hi]
    poke(profile.base_cmd, cmds)
    if tune.subtunes:
        for voice, order in enumerate(tune.subtunes[0].order_lists):
            poke(profile.base_order[voice], _encode_order(order))
    _lay_instruments(tune, profile, poke)
    _lay_pitch(tune, profile, poke)
    _lay_sequences(tune, profile, poke)


def _lay_pointers(tune: Tune, profile: Np20Profile, poke) -> None:
    def word(addr: int, value: int) -> None:
        poke(addr, [value & 0xFF, (value >> 8) & 0xFF])

    word(profile.ptr_init, profile.base_init_data)
    word(profile.ptr_pitch, profile.base_pitch)
    word(profile.ptr_wave, profile.base_wave_note)
    word(profile.ptr_filter, profile.base_filter)
    word(profile.ptr_pulse, profile.base_pulse)
    word(profile.ptr_inst, profile.base_inst)
    for voice in range(3):
        word(profile.ptr_order[voice], profile.base_order[voice])
    word(profile.ptr_seqlo, profile.base_seqlo)
    word(profile.ptr_seqhi, profile.base_seqhi)
    word(profile.ptr_cmd, profile.base_cmd)
    poke(profile.ptr_magic, list(profile.version_magic))
    tempo = tune.subtunes[0].tempo if tune.subtunes else 0
    poke(profile.base_init_data + 6, [tempo & 0xFF])


def write_editor_prg(
    tune: Tune, *, driver: bytes, profile: Np20Profile = NP20_PROFILE
) -> bytes:
    """Assemble an editor-native ``.prg`` for ``tune`` with the stock ``driver``.

    Lays the recovered tables at ``profile``'s canonical bases, writes the
    ``$0Fxx`` pointer block + version magic + default tempo, and returns the
    2-byte-load image (``load LE + bytes``).  Raises
    :class:`~pyjch.errors.SidParseError` on capacity overflow and
    :class:`NotImplementedError` for parameters this profile leaves unresolved.
    """
    _check_capacity(tune)
    mem: Dict[int, int] = {}

    def poke(addr: int, data) -> None:
        base = addr - profile.load_addr
        for offset, byte in enumerate(data):
            mem[base + offset] = byte & 0xFF

    for offset, byte in enumerate(driver):
        mem[offset] = byte
    _lay_tables(tune, profile, poke)
    _lay_pointers(tune, profile, poke)
    size = max(mem) + 1 if mem else 0
    image = bytearray(size)
    for offset, byte in mem.items():
        image[offset] = byte
    load = profile.load_addr
    return bytes([load & 0xFF, (load >> 8) & 0xFF]) + bytes(image)
