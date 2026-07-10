"""Write a recovered :class:`~pyjch.songmodel.Tune` as an editor-native ``.prg``.

Implements the write-side spec in ``docs/editor-format.md``: inject a
caller-supplied stock player-code prefix (``driver`` -- never embedded here, per
the no-copyrighted-material rule), lay the recovered tables at the profile's
canonical page-aligned data-region bases, encode them, write the ``$0FA0``
header pointer block + 5-char version magic + default tempo, and emit a
2-byte-load ``.prg`` at ``$0F00``.

One :class:`EditorProfile` carries the layout; NP20-25 share it byte-for-byte
(verified against the four NP22-25 songs on the release ``.d64``) and differ
only in the ``$0FEE`` version magic.  Tables that share the packed and editor
encodings transfer verbatim (instruments, wave cols, pulse/filter, command
table, pitch).  Two encodings are genuine packed->editor gaps:

* order lists -- the packed transpose baseline is unconfirmed, so the recovered
  ``OrderEntry.transpose`` byte is emitted verbatim (a note is recorded);
* sequences -- the closed-source packer's variable stream is re-encoded
  best-effort to editor ``(byte0, note)`` pairs; a packed control byte with no
  single-pair form is dropped and recorded in :attr:`Provenance.notes`.

The default empty ``driver`` yields a zero-filled player region: structurally
valid (correct pointer block / tables), but not runnable without a stock driver.
"""

from dataclasses import dataclass
from typing import Dict, List, Tuple

from pyjch.errors import SidParseError
from pyjch.songmodel import OrderList, Tune

_MAX_INSTRUMENTS = 32
_MAX_SUBTUNES = 31
_MAX_PATTERNS = 114
_MAX_ROWS = 96
_MAX_TABLE = 256

_SEQ_END = 0x7F
_SEQ_NOOP = 0x80
_ORDER_END = 0xFF
_TOP = 0x10000


@dataclass(frozen=True)
class EditorProfile:
    """Editor ``.prg`` layout: version magic, ``$0FA0`` index map, table bases.

    Every field but ``version_magic`` is shared across NP20-25 (verified from
    the release ``.d64``).  ``$0FA0`` pointer address for index ``i`` is
    ``ptr_base + i*2``; table bases are 256-byte aligned, order lists spaced by
    ``order_stride`` (``$400``), wave col 2 == wave col 1 ``+ 256``.
    """

    version_magic: bytes
    load_addr: int = 0x0F00
    init_addr: int = 0x1000
    play_addr: int = 0x1003
    ptr_base: int = 0x0FA0
    ptr_magic: int = 0x0FEE
    order_stride: int = 0x0400
    # $0FA0 array indices (see docs/editor-format.md).
    idx_init: int = 0x03
    idx_pitch: int = 0x0C
    idx_finetune: int = 0x0D
    idx_wave: int = 0x0E
    idx_wave2: int = 0x0F
    idx_filter: int = 0x10
    idx_pulse: int = 0x11
    idx_inst: int = 0x12
    idx_order: Tuple[int, int, int] = (0x13, 0x14, 0x15)
    idx_seqlo: int = 0x16
    idx_seqhi: int = 0x17
    idx_cmd: int = 0x18
    # Canonical page-aligned data-region bases.
    base_init_data: int = 0x0F00
    base_pitch: int = 0x1700
    base_finetune: int = 0x1800
    base_wave_note: int = 0x1900
    base_wave_ctrl: int = 0x1A00
    base_filter: int = 0x1B00
    base_pulse: int = 0x1C00
    base_inst: int = 0x1D00
    base_seqlo: int = 0x1E00
    base_seqhi: int = 0x1F00
    base_cmd: int = 0x2000
    base_order: Tuple[int, int, int] = (0x2100, 0x2500, 0x2900)
    base_seq: int = 0x2D00

    def ptr_addr(self, index: int) -> int:
        """Header-block address holding the pointer word for ``$0FA0`` index."""
        return self.ptr_base + index * 2


def _profile(magic: str) -> EditorProfile:
    return EditorProfile(version_magic=magic.encode("ascii"))


NP20_PROFILE = _profile("20.G4")
NP21_PROFILE = _profile("21.G5")
NP22_PROFILE = _profile("22.4X")
NP23_PROFILE = _profile("23.FX")
NP24_PROFILE = _profile("24.1X")
NP25_PROFILE = _profile("25.FX")

_NP2X = {22: NP22_PROFILE, 23: NP23_PROFILE, 24: NP24_PROFILE, 25: NP25_PROFILE}
_NP_ALL = {20: NP20_PROFILE, 21: NP21_PROFILE, **_NP2X}


def NP2X_PROFILE(version: int) -> EditorProfile:
    """Return the NP22-25 profile for ``version`` (22-25)."""
    try:
        return _NP2X[version]
    except KeyError as exc:
        raise SidParseError(f"no NP2X profile for version {version}") from exc


def np_profile(version: int) -> EditorProfile:
    """Return the profile for any supported NP version (20, 21, 22-25)."""
    try:
        return _NP_ALL[version]
    except KeyError as exc:
        raise SidParseError(f"no editor profile for NP version {version}") from exc


def _encode_order(order: OrderList) -> List[int]:
    """Editor ``(transpose, seq_index)`` pairs; ``$FF`` transpose terminates.

    The recovered transpose is emitted verbatim: the packed->editor baseline is
    an unconfirmed packer transform (see docs/editor-format.md).
    """
    out: List[int] = []
    for entry in order.entries:
        out += [entry.transpose & 0xFF, entry.pattern & 0xFF]
    out.append(_ORDER_END)
    return out


def _encode_sequence(index: int, events) -> Tuple[List[int], List[str]]:
    """Best-effort packed pattern stream -> editor ``(byte0, note)`` pairs.

    Folds a preceding instrument select (``$A0-$BF``) into the next note's
    ``byte0``; emits a command (``$C0+``) as its own row; a packed ``$80-$9F``
    control byte has no single editor-pair form and is dropped + noted.
    """
    out: List[int] = []
    notes: List[str] = []
    pending = _SEQ_NOOP
    for event in events:
        raw = event.raw
        if event.note is not None:
            out += [pending, raw]
            pending = _SEQ_NOOP
        elif 0xA0 <= raw <= 0xBF:
            pending = raw
        elif raw >= 0xC0:
            out += [raw, 0x00]
        else:
            notes.append(
                f"sequence {index}: packed control byte ${raw:02X} has no single "
                "editor-pair form (dropped)"
            )
    out.append(_SEQ_END)
    return out, notes


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


def _require_tables(tune: Tune, profile: EditorProfile) -> None:
    magic = profile.version_magic.decode("ascii")
    tier = tune.provenance.tier
    if tune.wavetable is None or not tune.wavetable.ctrl_col:
        raise SidParseError(
            f"NP{magic} export requires the wave table (both columns); tune "
            f"(tier {tier}) has none -- editor form cannot be emitted"
        )
    if not tune.pitch_table:
        raise SidParseError(
            f"NP{magic} export requires the pitch table; tune (tier {tier}) "
            "has none -- editor form cannot be emitted"
        )


def _fits(data: List[int], limit: int, what: str) -> List[int]:
    if len(data) > limit:
        raise SidParseError(f"{what} overflows ({len(data)} > {limit} bytes)")
    return data


def _lay_tables(tune: Tune, profile: EditorProfile, poke, notes: List[str]) -> None:
    wave = tune.wavetable
    poke(profile.base_wave_note, _fits(wave.note_col, _MAX_TABLE, "wave note col"))
    poke(profile.base_wave_ctrl, _fits(wave.ctrl_col, _MAX_TABLE, "wave ctrl col"))
    poke(profile.base_pitch, _fits(tune.pitch_table, _MAX_TABLE, "pitch table"))
    filt: List[int] = []
    for step in tune.filter_program:
        filt += [step.value, step.step, step.dwell, step.next_off]
    poke(profile.base_filter, _fits(filt, _MAX_TABLE, "filter table"))
    pulse: List[int] = []
    for step in tune.pw_program:
        pulse += [step.reset, step.step, step.dir_rate, step.next_off]
    poke(profile.base_pulse, _fits(pulse, _MAX_TABLE, "pulse table"))
    cmds: List[int] = []
    for cmd in tune.commands:
        cmds += [cmd.lo, cmd.hi]
    poke(profile.base_cmd, _fits(cmds, _MAX_TABLE, "command table"))
    for index, inst in enumerate(tune.instruments):
        poke(profile.base_inst + index * 8, inst.raw[:8])
    if tune.subtunes:
        notes.append("order-list transpose emitted verbatim (packer baseline unknown)")
        for voice, order in enumerate(tune.subtunes[0].order_lists):
            data = _encode_order(order)
            if len(data) > profile.order_stride:
                raise SidParseError(
                    f"order list voice {voice} overflows "
                    f"({len(data)} > {profile.order_stride} bytes)"
                )
            poke(profile.base_order[voice], data)
    _lay_sequences(tune, profile, poke, notes)


def _lay_sequences(tune: Tune, profile: EditorProfile, poke, notes: List[str]) -> None:
    addr = profile.base_seq
    for index, events in enumerate(tune.patterns):
        data, seq_notes = _encode_sequence(index, events)
        notes.extend(seq_notes)
        if addr + len(data) > _TOP:
            raise SidParseError("sequence data overflows 64K image")
        poke(profile.base_seqlo + index, [addr & 0xFF])
        poke(profile.base_seqhi + index, [(addr >> 8) & 0xFF])
        poke(addr, data)
        addr += len(data)


def _lay_pointers(tune: Tune, profile: EditorProfile, poke) -> None:
    def word(index: int, value: int) -> None:
        poke(profile.ptr_addr(index), [value & 0xFF, (value >> 8) & 0xFF])

    word(profile.idx_init, profile.base_init_data)
    word(profile.idx_pitch, profile.base_pitch)
    word(profile.idx_finetune, profile.base_finetune)
    word(profile.idx_wave, profile.base_wave_note)
    word(profile.idx_wave2, profile.base_wave_ctrl)
    word(profile.idx_filter, profile.base_filter)
    word(profile.idx_pulse, profile.base_pulse)
    word(profile.idx_inst, profile.base_inst)
    for voice in range(3):
        word(profile.idx_order[voice], profile.base_order[voice])
    word(profile.idx_seqlo, profile.base_seqlo)
    word(profile.idx_seqhi, profile.base_seqhi)
    word(profile.idx_cmd, profile.base_cmd)
    poke(profile.ptr_magic, list(profile.version_magic))
    tempo = tune.subtunes[0].tempo if tune.subtunes else 0
    poke(profile.base_init_data + 6, [tempo & 0xFF])


def write_editor_prg(
    tune: Tune, *, driver: bytes = b"", profile: EditorProfile = NP25_PROFILE
) -> bytes:
    """Assemble an editor-native ``.prg`` for ``tune`` at the profile's layout.

    Injects the caller-supplied stock ``driver`` prefix (default empty ->
    zero-filled, non-runnable but structurally valid), lays the recovered tables
    at ``profile``'s canonical bases, writes the ``$0FA0`` pointer block + 5-char
    version magic + default tempo, and returns the 2-byte-load image.  Records
    the order-transpose / dropped-sequence-byte gaps in
    :attr:`~pyjch.songmodel.Provenance.notes`.  Raises
    :class:`~pyjch.errors.SidParseError` on capacity overflow or when the tune
    lacks a table the editor form requires.
    """
    _check_capacity(tune)
    _require_tables(tune, profile)
    mem: Dict[int, int] = {}

    def poke(addr: int, data) -> None:
        base = addr - profile.load_addr
        for offset, byte in enumerate(data):
            mem[base + offset] = byte & 0xFF

    for offset, byte in enumerate(driver):
        mem[offset] = byte
    notes: List[str] = []
    _lay_tables(tune, profile, poke, notes)
    _lay_pointers(tune, profile, poke)
    tune.provenance.notes.extend(notes)
    size = max(mem) + 1 if mem else 0
    image = bytearray(size)
    for offset, byte in mem.items():
        image[offset] = byte
    load = profile.load_addr
    return bytes([load & 0xFF, (load >> 8) & 0xFF]) + bytes(image)
