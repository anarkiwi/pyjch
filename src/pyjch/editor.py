"""Write a recovered :class:`~pyjch.songmodel.Tune` as an editor-native ``.prg``.

Implements the write-side spec in ``docs/editor-format.md``: take a
caller-supplied stock player-code prefix (``driver`` -- never embedded here, per
the no-copyrighted-material rule; the file's own bytes from ``$0F00`` up to its
first table), read every table base from the driver's own ``$0FA0`` pointer
block (the player code addresses the tables with absolute operands, so they must
stay where the driver expects them -- NP20/21/24 differ from NP22/23/25),
check each table fits before the next base, encode the recovered tables there,
write the init-data block at the driver's ``ptr[3]`` (three order-list words,
tempo at ``+6``) and the 5-char version magic, and emit a 2-byte-load ``.prg``
at ``$0F00``.

The packer does **not** re-encode musical data (see ``docs/editor-format.md``
*Packer transform*): sequences, order lists, instruments, wave/pulse/filter/
command and pitch tables are byte-identical between packed and editor forms.
So export copies the recovered bytes **verbatim** into the driver's layout and
rebuilds only the sequence lo/hi pointer tables, the init-data block and the
default tempo.  Nothing is written below the first table base, and an empty
driver raises: bases are never invented.

:func:`read_editor_prg` inverts the writer: it recovers the song structures
from an emitted image via the ``$0FA0`` pointer block, so an export round-trips
back to the same order lists, sequences and tables (see the round-trip tests).
"""

import re
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Tuple

from pysidtracker import load_prefix

from pyjch import grammar
from pyjch.errors import SidParseError
from pyjch.newplayer import image_of
from pyjch.songmodel import OrderEntry, Tune

_MAX_INSTRUMENTS = 32
_MAX_SUBTUNES = 31
_MAX_PATTERNS = 114
_MAX_ROWS = 96
_MAX_TABLE = 256

_TOP = 0x10000
_MARKS = (0xFE, 0xFF)


@dataclass(frozen=True)
class EditorProfile:
    """Editor ``.prg`` layout: version magic and ``$0FA0`` index map.

    Every field but ``version_magic`` is shared across NP20-25.  ``$0FA0``
    pointer address for index ``i`` is ``ptr_base + i*2``; order lists are
    spaced by ``order_stride`` (``$400``).  Table *bases* are not part of the
    profile: they differ per driver build and are read from the driver.
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


_MAGIC_RE = re.compile(rb"\d\d\.[0-9A-Za-z]{2}")
_NAME_RE = re.compile(rb"(\d\d\.[0-9A-Za-z]{2})\s*$")
_NO_MAGIC = bytes(5)


def profile_for_magic(magic: bytes) -> EditorProfile:
    """Return the profile for any work-file ``NN.xx`` magic, or the zero magic.

    Every JCH editor work file shares the ``$0FA0`` pointer block; only the
    5-char ``$0FEE`` version string differs, so any well-formed magic (NP04-NP25,
    ``21.G4``/``21.G6``, ``10.G1`` ...) is accepted.  The six writer profiles are
    returned as themselves; five zero bytes (NP04.13, 05.02, 06.01: no magic)
    give a profile with that zero magic.  Raises
    :class:`~pyjch.errors.SidParseError` for anything else.
    """
    magic = bytes(magic)
    for known in _NP_ALL.values():
        if known.version_magic == magic:
            return known
    if magic != _NO_MAGIC and not _MAGIC_RE.fullmatch(magic):
        raise SidParseError(f"not an editor work file: magic {magic!r}")
    return EditorProfile(version_magic=magic)


def version_from_name(name: str) -> Optional[str]:
    """Version (``NN.xx``) carried by a work-file name such as ``NAME/05.02``."""
    found = _NAME_RE.search(name.encode("latin-1", "replace"))
    return found.group(1).decode("ascii") if found else None


def profile_for_tune(tune: Tune) -> EditorProfile:
    """Return the editor profile matching ``tune``'s own player version.

    Only V20 tunes have a faithful editor target (``20.G4``).  Classic-family
    tunes (V9-V18 etc.) have their own ``NN.Gx`` work-file formats with no
    profile here yet, so they raise rather than being stamped as another version.
    """
    if tune.provenance.tier == "v20":
        return NP20_PROFILE
    raise SidParseError(
        f"no editor profile for player {tune.provenance.version!r} "
        f"(tier {tune.provenance.tier}): only V20 (20.G4) tunes can be exported"
    )


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


def _require_version(tune: Tune, profile: EditorProfile) -> None:
    """Refuse a cross-version export: table semantics differ per player version."""
    native = profile_for_tune(tune)
    if profile.version_magic != native.version_magic:
        raise SidParseError(
            f"cannot export a {native.version_magic.decode('ascii')} tune as "
            f"{profile.version_magic.decode('ascii')}: command, instrument and "
            "replay-speed semantics differ between player versions"
        )


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


class _Layout:
    """Table bases read from a driver's ``$0FA0`` block, with room per table."""

    def __init__(self, driver: bytes, profile: EditorProfile) -> None:
        if not driver:
            raise SidParseError(
                "editor export needs the stock driver (the work file's bytes "
                "from $0F00 to its first table); table bases are taken from "
                "its $0FA0 pointer block and are never invented"
            )
        self.profile = profile
        self.driver = driver
        self.idx = {
            "init": profile.idx_init,
            "pitch": profile.idx_pitch,
            "finetune": profile.idx_finetune,
            "wave": profile.idx_wave,
            "wave2": profile.idx_wave2,
            "filter": profile.idx_filter,
            "pulse": profile.idx_pulse,
            "inst": profile.idx_inst,
            "order0": profile.idx_order[0],
            "order1": profile.idx_order[1],
            "order2": profile.idx_order[2],
            "seqlo": profile.idx_seqlo,
            "seqhi": profile.idx_seqhi,
            "cmd": profile.idx_cmd,
        }
        self.base = {name: self._ptr(index) for name, index in self.idx.items()}
        order_end = max(self.base[f"order{v}"] for v in range(3)) + profile.order_stride
        self.seq_base = order_end
        starts = sorted(set(self.base.values()) | {order_end})
        for addr in starts:
            if not profile.load_addr <= addr < _TOP:
                raise SidParseError(f"driver table base ${addr:04X} out of range")
        self.floor = min(self.base.values())
        self._starts = starts

    def _ptr(self, index: int) -> int:
        offset = self.profile.ptr_addr(index) - self.profile.load_addr
        if offset < 0 or offset + 2 > len(self.driver):
            raise SidParseError(
                f"driver too short: no $0FA0 pointer {index:#04x} "
                f"({len(self.driver)} bytes)"
            )
        return self.driver[offset] | (self.driver[offset + 1] << 8)

    def room(self, name: str) -> int:
        """Bytes from the table's base up to the next table base."""
        base = self.base[name]
        later = [a for a in self._starts if a > base]
        return (later[0] if later else _TOP) - base


def _lay_tables(tune: Tune, layout: _Layout, poke) -> None:
    base = layout.base
    wave = tune.wavetable

    def put(name: str, data: List[int], what: str) -> None:
        poke(base[name], _fits(data, min(_MAX_TABLE, layout.room(name)), what))

    put("wave", wave.note_col, "wave note col")
    put("wave2", wave.ctrl_col, "wave ctrl col")
    put("pitch", tune.pitch_table, "pitch table")
    filt: List[int] = []
    for step in tune.filter_program:
        filt += [step.value, step.step, step.dwell, step.next_off]
    put("filter", filt, "filter table")
    pulse: List[int] = []
    for step in tune.pw_program:
        pulse += [step.reset, step.step, step.dir_rate, step.next_off]
    put("pulse", pulse, "pulse table")
    cmds: List[int] = []
    for cmd in tune.commands:
        cmds += [cmd.lo, cmd.hi]
    put("cmd", cmds, "command table")
    insts: List[int] = []
    for inst in tune.instruments:
        insts += list(inst.raw[:8])
    _fits(insts, layout.room("inst"), "instrument table")
    poke(base["inst"], insts)
    limit = layout.profile.order_stride
    for voice in range(3 if tune.subtunes else 0):
        data = _voice_block(tune, voice)
        cap = min(limit, layout.room(f"order{voice}"))
        if len(data) > cap:
            raise SidParseError(
                f"order list voice {voice} overflows ({len(data)} > {cap} bytes)"
            )
        poke(base[f"order{voice}"], data)
    _lay_sequences(tune, layout, poke)


def _mark_pair(raw: List[int]) -> List[int]:
    """``raw`` with its ``$FF``/``$FE`` terminator widened to a 2-byte mark pair."""
    data = list(raw)
    if data and data[-1] in _MARKS:
        data.append(0x00)  # the second mark byte is ignored by the players
    return data


def _voice_block(tune: Tune, voice: int) -> List[int]:
    """One voice's track table: every subtune back to back, then a double mark.

    Each subtune's order bytes are emitted verbatim (transpose + sequence pairs
    ending in a single ``FF 00``/``FE 00`` mark); the packer finds the tune count
    from the extra mark closing the block.
    """
    block: List[int] = []
    for sub in tune.subtunes:
        block += _mark_pair(sub.order_lists[voice].raw)
    return block + block[-2:]


def _lay_sequences(tune: Tune, layout: _Layout, poke) -> None:
    count = len(tune.pattern_raw)
    for name in ("seqlo", "seqhi"):
        if count > layout.room(name):
            raise SidParseError(f"sequence {name} table overflows ({count} entries)")
    addr = layout.seq_base
    for index, raw in enumerate(tune.pattern_raw):
        if addr + len(raw) > _TOP:
            raise SidParseError("sequence data overflows 64K image")
        poke(layout.base["seqlo"] + index, [addr & 0xFF])
        poke(layout.base["seqhi"] + index, [(addr >> 8) & 0xFF])
        poke(addr, raw)  # verbatim: source pattern bytes incl. $7F terminator
        addr += len(raw)


def _lay_init_data(tune: Tune, layout: _Layout, poke) -> None:
    """Init-data block at ``ptr[3]``: three order words, tempo at ``+6``."""
    if layout.room("init") < 7:
        raise SidParseError("init-data block does not fit before the next table")
    init = layout.base["init"]
    for voice in range(3):
        addr = layout.base[f"order{voice}"]
        poke(init + voice * 2, [addr & 0xFF, (addr >> 8) & 0xFF])
    tempo = tune.subtunes[0].tempo if tune.subtunes else 0
    poke(init + 6, [tempo & 0xFF])  # +7 is left alone


def write_editor_prg(
    tune: Tune, *, driver: bytes, profile: Optional[EditorProfile] = None
) -> bytes:
    """Assemble an editor-native ``.prg`` for ``tune`` on ``driver``'s layout.

    ``driver`` is the headerless work-file prefix loaded at ``$0F00`` (player
    code plus its own ``$0FA0`` pointer block, up to the first table).  Every
    table base is read from that block.  Splits any sequence exceeding the
    editor's 96-row cap into consecutive row-aligned chunks (rewriting the
    order lists), copies the recovered sequence / order / table bytes
    **verbatim** to the driver's bases, writes the init-data block, the sequence
    lo/hi tables and the 5-char version magic, and returns the 2-byte-load
    image.  ``profile`` defaults to the tune's own player version
    (:func:`profile_for_tune`); a profile of another version is refused.  Raises :class:`~pyjch.errors.SidParseError` for an empty or
    malformed driver, a version mismatch, on a table that does not fit before the next base, or when
    the tune lacks a table the editor form requires.
    """
    if profile is None:
        profile = profile_for_tune(tune)
    else:
        _require_version(tune, profile)
    layout = _Layout(driver, profile)
    tune = _split_long_patterns(tune)
    _check_capacity(tune)
    _require_tables(tune, profile)
    for _stream in grammar.tune_streams(tune):
        pass
    mem: Dict[int, int] = {}
    floor = layout.floor

    def poke(addr: int, data) -> None:
        if addr < floor:
            raise SidParseError(f"write at ${addr:04X} below first table ${floor:04X}")
        for offset, byte in enumerate(data):
            mem[addr - profile.load_addr + offset] = byte & 0xFF

    for offset, byte in enumerate(driver):
        mem[offset] = byte
    _lay_tables(tune, layout, poke)
    _lay_init_data(tune, layout, poke)
    for offset, byte in enumerate(profile.version_magic):
        mem[profile.ptr_magic - profile.load_addr + offset] = byte
    size = max(mem) + 1
    image = bytearray(size)
    for offset, byte in mem.items():
        image[offset] = byte
    return load_prefix(profile.load_addr) + bytes(image)


@dataclass(frozen=True)
class EditorImage:
    """Song structures recovered from an editor ``.prg`` via its ``$0FA0`` block.

    The self-terminating music data -- per-voice order lists (``$FE``/``$FF``)
    and sequences (``$7F``) -- is recovered byte-for-byte with no out-of-band
    length.  :meth:`table` reads a terminatorless table (pitch / wave / pulse /
    filter / instrument / command) verbatim at its pointer-block base for a
    caller-supplied length.
    """

    profile: EditorProfile
    load_addr: int
    image: bytes
    magic: bytes
    tempo: int
    order_lists: List[List[int]]
    sequences: List[List[int]]
    tunes: List[List[List[int]]] = field(default_factory=list)
    version: Optional[str] = None  # ``NN.xx``: from the magic, else the file name

    def _byte(self, addr: int) -> int:
        offset = addr - self.load_addr
        if not 0 <= offset < len(self.image):
            raise SidParseError(f"address ${addr:04X} outside editor image")
        return self.image[offset]

    def base(self, index: int) -> int:
        """Resolve the ``$0FA0`` pointer word for ``index`` to its table base."""
        addr = self.profile.ptr_addr(index)
        return self._byte(addr) | (self._byte(addr + 1) << 8)

    def table(self, index: int, length: int) -> List[int]:
        """Read ``length`` bytes of the table at ``$0FA0`` ``index`` verbatim."""
        base = self.base(index)
        return [self._byte(base + offset) for offset in range(length)]


def read_editor_prg(
    prg: bytes, *, profile: Optional[EditorProfile] = None, name: Optional[str] = None
) -> EditorImage:
    """Invert :func:`write_editor_prg`: recover an editor ``.prg``'s structures.

    Walks each voice's track table, splitting at
    single ``FE``/``FF`` marks (one tune each) and stopping at the first double
    mark, so multi-tune work files recover every tune.

    Reads the ``$0FA0`` pointer block for every table base, the ``$0FEE`` magic
    and default tempo, then recovers the self-terminating music data -- each
    voice order list (to ``$FE``/``$FF``) and every referenced sequence (to
    ``$7F``) -- byte-for-byte with no out-of-band length.  Terminatorless tables
    are read on demand through :meth:`EditorImage.table`.  Raises
    :class:`~pyjch.errors.SidParseError` on a truncated image or a magic that
    does not match ``profile``.

    With no ``profile`` the version is taken from the file's own ``$0FEE``
    magic (:func:`profile_for_magic`): any ``NN.xx`` string is accepted, and a
    zero magic (NP04-NP06 work files) falls back to the ``NN.xx`` suffix of
    ``name``.  Every table is resolved through the pointer words, never profile
    constants.  :attr:`EditorImage.version` reports the identified version
    (``None`` when neither magic nor name carries one).
    """
    if len(prg) < 2:
        raise SidParseError("editor .prg too short")
    load = prg[0] | (prg[1] << 8)
    image = bytes(prg[2:])

    def byte(addr: int) -> int:
        offset = addr - load
        if not 0 <= offset < len(image):
            raise SidParseError(f"address ${addr:04X} outside editor image")
        return image[offset]

    def word(addr: int) -> int:
        return byte(addr) | (byte(addr + 1) << 8)

    if profile is None:
        probe = EditorProfile(version_magic=_NO_MAGIC)
        profile = profile_for_magic(
            bytes(byte(probe.ptr_magic + offset) for offset in range(5))
        )
    magic = bytes(
        byte(profile.ptr_magic + offset) for offset in range(len(profile.version_magic))
    )
    if magic != profile.version_magic:
        raise SidParseError(
            f"editor magic {magic!r} != profile {profile.version_magic!r}"
        )
    version = magic.decode("ascii") if magic != _NO_MAGIC else None
    if version is None and name is not None:
        version = version_from_name(name)
    tempo = byte(word(profile.ptr_addr(profile.idx_init)) + 6)

    img = image_of(load, image)

    def stream(addr: int, rule: str, what: str, reach: int):
        size = min(reach, load + len(image) - addr)
        if size <= 0:
            raise SidParseError(f"{what} at ${addr:04X} outside editor image")
        return grammar.read_stream(img, addr, rule, what, size)

    tunes: List[List[List[int]]] = []
    referenced: set = set()
    for voice in range(3):
        addr = word(profile.ptr_addr(profile.idx_order[voice]))
        voice_tunes: List[List[int]] = []
        while True:
            order = stream(
                addr, grammar.ORDERLIST, f"order list {voice}", grammar.ORDERLIST_REACH
            )
            referenced.update(pattern for pattern, _transpose in order.steps)
            addr += len(order.raw)
            # the byte after a mark is ignored by the players
            voice_tunes.append(list(order.raw) + [byte(addr)])
            addr += 1
            # a single mark separates tunes; consecutive marks end the block
            if byte(addr) in _MARKS:
                break
        for k, tune_raw in enumerate(voice_tunes):
            if k == len(tunes):
                tunes.append([])
            tunes[k].append(tune_raw)
    order_lists = [raw[:-1] for raw in tunes[0]]

    seqlo = word(profile.ptr_addr(profile.idx_seqlo))
    seqhi = word(profile.ptr_addr(profile.idx_seqhi))
    sequences: List[List[int]] = []
    for index in range(max(referenced) + 1 if referenced else 0):
        addr = byte(seqlo + index) | (byte(seqhi + index) << 8)
        rows = stream(addr, grammar.PATTERN, f"sequence {index}", grammar.PATTERN_REACH)
        sequences.append(list(rows.raw))

    return EditorImage(
        profile=profile,
        load_addr=load,
        image=image,
        magic=magic,
        tempo=tempo,
        order_lists=order_lists,
        sequences=sequences,
        tunes=tunes,
        version=version,
    )
