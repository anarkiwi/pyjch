"""Write a recovered :class:`~pyjch.songmodel.Tune` as an editor-native ``.prg``.

Implements the write-side spec in ``docs/editor-format.md``: inject a
caller-supplied stock player-code prefix (``driver`` -- never embedded here, per
the no-copyrighted-material rule), lay the recovered tables at the profile's
canonical page-aligned data-region bases, encode them, write the ``$0FA0``
header pointer block + 5-char version magic + default tempo, and emit a
2-byte-load ``.prg`` at ``$0F00``.

One :class:`EditorProfile` carries the layout; NP20-25 share it byte-for-byte
(verified against the four NP22-25 songs on the release ``.d64``) and differ
only in the ``$0FEE`` version magic.  The packer does **not** re-encode musical
data (see ``docs/editor-format.md`` *Packer transform*): sequences, order lists,
instruments, wave/pulse/filter/command and pitch tables are byte-identical
between packed and editor forms.  So export copies the recovered bytes
**verbatim** into the page-aligned editor layout and rebuilds only the structure
around them: the ``$0FA0`` pointer array, the sequence lo/hi pointer tables, the
``$0FEE`` magic and the default tempo.  No sequence re-encoding, no order
transpose-baseline synthesis.

The default empty ``driver`` yields a zero-filled player region: structurally
valid (correct pointer block / tables), but not runnable without a stock driver.
"""

from dataclasses import dataclass, replace
from typing import Dict, List, Tuple

from pyjch.errors import SidParseError
from pyjch.songmodel import OrderEntry, Tune

_MAX_INSTRUMENTS = 32
_MAX_SUBTUNES = 31
_MAX_PATTERNS = 114
_MAX_ROWS = 96
_MAX_TABLE = 256

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


def _pattern_rows(raw: List[int]) -> int:
    """Editor rows in a sequence ``raw`` stream (through the ``$7F`` terminator).

    A row is a player fetch unit: zero or more command bytes (``>= $80``)
    followed by exactly one note byte (``< $80``); ``$7F`` ends the sequence
    (peeked only right after a note).  The editor's 96-row cap counts these
    fetch units, not raw bytes.
    """
    rows = 0
    for byte in raw:
        if byte == 0x7F:
            break
        if byte < 0x80:
            rows += 1
    return rows


def _split_raw(raw: List[int], max_rows: int) -> List[List[int]]:
    """Split a ``$7F``-terminated sequence into row-aligned ``<= max_rows`` chunks.

    Boundaries fall only after a note byte (a player row boundary), where the
    player resets the pattern cursor and advances the order pointer -- per-voice
    state persists, so the split is behaviourally identical.  Each chunk is
    ``$7F``-terminated; concatenating the chunks minus their injected ``$7F``
    reproduces the original body exactly.
    """
    if not raw or raw[-1] != 0x7F:
        return [list(raw)]
    body = raw[:-1]
    bounds = [k + 1 for k, byte in enumerate(body) if byte < 0x80]
    if not bounds or bounds[-1] != len(body):
        bounds.append(len(body))  # trailing partial row (no closing note)
    chunks: List[List[int]] = []
    start = 0
    for group in (bounds[i : i + max_rows] for i in range(0, len(bounds), max_rows)):
        end = group[-1]
        chunks.append(list(body[start:end]) + [0x7F])
        start = end
    return chunks


def _split_long_patterns(tune: Tune, max_rows: int = _MAX_ROWS) -> Tune:
    """Split any sequence exceeding ``max_rows`` rows into consecutive chunks.

    Rewrites the order lists (byte-level, preserving each transpose prefix -- the
    player's persistent transpose covers a chunk run) so a voice's order list
    references the chunk sequences in order.  Returns ``tune`` unchanged when no
    sequence overflows.
    """
    expand: Dict[int, List[int]] = {}
    patterns = list(tune.patterns)
    pattern_raw = list(tune.pattern_raw)
    for index, raw in enumerate(tune.pattern_raw):
        if _pattern_rows(raw) <= max_rows:
            continue
        chunks = _split_raw(raw, max_rows)
        events = patterns[index]
        offsets = [0]
        for chunk in chunks:
            offsets.append(offsets[-1] + len(chunk) - 1)  # drop injected $7F
        idxs = [index]
        pattern_raw[index] = chunks[0]
        patterns[index] = events[offsets[0] : offsets[1]]
        for cnum in range(1, len(chunks)):
            idxs.append(len(pattern_raw))
            pattern_raw.append(chunks[cnum])
            patterns.append(events[offsets[cnum] : offsets[cnum + 1]])
        expand[index] = idxs
    if not expand:
        return tune
    subtunes = [
        replace(sub, order_lists=[_remap_order(o, expand) for o in sub.order_lists])
        for sub in tune.subtunes
    ]
    return replace(tune, patterns=patterns, pattern_raw=pattern_raw, subtunes=subtunes)


def _remap_order(order, expand: Dict[int, List[int]]):
    """Expand every split pattern reference in an order list to its chunk run."""
    raw: List[int] = []
    for byte in order.raw:
        if byte < 0x80 and byte in expand:
            raw.extend(expand[byte])  # chunk indices (all < 0x80); transpose persists
        else:
            raw.append(byte)
    entries: List[OrderEntry] = []
    for entry in order.entries:
        for pat in expand.get(entry.pattern, [entry.pattern]):
            entries.append(OrderEntry(pattern=pat, transpose=entry.transpose))
    return replace(order, raw=raw, entries=entries)


def _check_capacity(tune: Tune) -> None:
    if len(tune.instruments) > _MAX_INSTRUMENTS:
        raise SidParseError(f"too many instruments ({len(tune.instruments)} > 32)")
    if len(tune.subtunes) > _MAX_SUBTUNES:
        raise SidParseError(f"too many subtunes ({len(tune.subtunes)} > 31)")
    if len(tune.patterns) > _MAX_PATTERNS:
        raise SidParseError(f"too many patterns ({len(tune.patterns)} > 114)")
    for index, raw in enumerate(tune.pattern_raw):
        rows = _pattern_rows(raw)
        if rows > _MAX_ROWS:
            raise SidParseError(f"pattern {index} too long ({rows} > 96 rows)")


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


def _lay_tables(tune: Tune, profile: EditorProfile, poke) -> None:
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
        for voice, order in enumerate(tune.subtunes[0].order_lists):
            data = order.raw  # verbatim: carries transpose + $FF/$FE terminator
            if len(data) > profile.order_stride:
                raise SidParseError(
                    f"order list voice {voice} overflows "
                    f"({len(data)} > {profile.order_stride} bytes)"
                )
            poke(profile.base_order[voice], data)
    _lay_sequences(tune, profile, poke)


def _lay_sequences(tune: Tune, profile: EditorProfile, poke) -> None:
    addr = profile.base_seq
    for index, raw in enumerate(tune.pattern_raw):
        if addr + len(raw) > _TOP:
            raise SidParseError("sequence data overflows 64K image")
        poke(profile.base_seqlo + index, [addr & 0xFF])
        poke(profile.base_seqhi + index, [(addr >> 8) & 0xFF])
        poke(addr, raw)  # verbatim: source pattern bytes incl. $7F terminator
        addr += len(raw)


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
    zero-filled, non-runnable but structurally valid), splits any sequence
    exceeding the editor's 96-row cap into consecutive row-aligned chunks
    (rewriting the order lists), copies the recovered sequence / order / table
    bytes **verbatim** to ``profile``'s canonical bases, writes the ``$0FA0``
    pointer block + 5-char version magic + default tempo, and returns the
    2-byte-load image.  Raises
    :class:`~pyjch.errors.SidParseError` on capacity overflow or when the tune
    lacks a table the editor form requires.
    """
    tune = _split_long_patterns(tune)
    _check_capacity(tune)
    _require_tables(tune, profile)
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
