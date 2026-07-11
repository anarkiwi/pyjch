"""Version-aware reader for the JCH *NewPlayer wavetable family*.

pyjch's byte-exact :mod:`~pyjch.player` models one specific routine (the
canonical ``JCH_NewPlayer_V0x`` layout).  HVSC, however, holds thousands of
tunes built by the *later* JCH NewPlayer engine -- the two-column wavetable
player fully reverse-engineered in
``re-trackers/JCH_NewPlayer/{jch-architecture.md,jch-player.asm}`` (that
disassembly is a ``JCH_NewPlayer_V20`` tune, the largest HVSC bucket).

The V20 build of this family is replayed byte-exactly by the native engine in
:mod:`~pyjch.player`; every other recovered version plays byte-exactly too, by
running the tune's own 6502 driver on py65 via
:class:`~pysidtracker.EmuPlayer`.  This reader is a separate concern: it
recovers the **song DATA**, which is laid out the same way across the whole
family -- only relocated -- directly from the loaded image:

* a **subtune table** ``{orderptr v0,v1,v2, tempo, ...}`` per subtune,
* per-voice **order lists** (pattern-index streams, ``$FE`` stop / ``$FF``
  loop / ``$80+`` transpose),
* **pattern pointer** low/high tables (pattern base = ``hi<<8 | lo``),
* 8-byte **instrument** records,
* the note-frequency **pitch table**, both **wavetable columns** (note + CTRL),
  and (where present) the command / filter / PW-program tables -- the extra
  tables lifted by generalizing the V20 idioms by role (per-build work cells
  wildcarded), so the family reaches the same table coverage the V20 build does.

Each table base is discovered by its surrounding player-code instruction
idiom (the same relocation-safe idiom search the V0x reader uses), not a
fixed offset.  :func:`recover` returns a :class:`NewPlayerModel` only when
the discovered tables form a **coherent** song (bases in range, order lists
terminate within the image, pattern pointers in range); otherwise it raises,
so it never emits garbage from a foreign player.

This is a *reader*, not a player: a recovered model is the recovered song
structure.  Byte-exact playback is a separate concern (native for V20, via
:class:`~pysidtracker.EmuPlayer` for the rest).  See ``docs/versions.md``.
"""

from dataclasses import dataclass
from typing import List, Optional, Tuple

from pysidtracker import CodePattern, find_code_all, find_code_first

from pyjch.errors import SidParseError

# Order-list control bytes.
_ORD_STOP = 0xFE
_ORD_LOOP = 0xFF

_SUBTUNE_RECORD = 8  # bytes per subtune record {v0lo,hi,v1lo,hi,v2lo,hi,tempo,?}
_MAX_ORDER_WALK = 512  # order-list length cap for the coherence walk


def _op(image: bytes, spec: str) -> Optional[int]:
    """First ``{op}`` operand captured by ``spec`` in ``image``, or ``None``.

    ``spec`` is a :class:`~pysidtracker.CodePattern` string: fixed opcodes plus
    ``{op:w}`` for the relocated table base baked into the instruction operand.
    Each engine idiom is one such masked code fragment.
    """
    match = find_code_first(image, spec)
    return None if match is None else match.captures["op"]


def _w(value: int) -> str:
    """A 16-bit value as two little-endian ``CodePattern`` literal tokens."""
    return f"{value & 0xFF:02X} {(value >> 8) & 0xFF:02X}"


def _find_instrument_base(image: bytes) -> Optional[int]:
    """``LDA inst,Y ; LDY stride,X ; STA $D405,Y`` -> instrument-record base."""
    return _op(image, "B9 {op:w} BC ?? ?? 99 05 D4")


def _find_subtune_base(image: bytes) -> Optional[int]:
    """``LDX #$00 ; LDA subtune,Y ; STA abs,X`` -> subtune-table base."""
    return _op(image, "A2 00 B9 {op:w} 9D")


def _find_ctrl_shadow(image: bytes) -> Optional[int]:
    """CTRL-shadow cell from the blit ``LDA shadow,X .. AND gate,X ; STA $D404,Y``.

    The shadow is the per-voice CTRL byte ANDed with the gate mask and written
    to ``$D404,Y`` every frame.  ``LDA shadow,X`` (``BD``) sits immediately
    before ``AND gate,X`` (V13+ builds) or one ``LDY stride,X`` (``BC``) earlier
    (V9-11 builds).
    """
    for spec in (
        "BD {op:w} 3D ?? ?? 99 04 D4",  # V13+: LDA shadow,X ; AND gate,X ; STA
        "BD {op:w} BC ?? ?? 3D ?? ?? 99 04 D4",  # V9-11: + LDY stride,X
    ):
        base = _op(image, spec)
        if base is not None:
            return base
    return None


def _find_note_column(image: bytes, wave_ctrl: int, wtptr: int) -> Optional[int]:
    """Parallel note column paired with ``wave_ctrl`` under pointer ``wtptr``.

    Prefers the ``$7F``-jump handler ``LDA ctrl,Y ; STA wtptr,X ; TAY ;
    LDA note,Y`` (which ties both columns to the same pointer); falls back to
    the note-column read ``LDY wtptr,X ; LDA note,Y`` gated by its column test
    (``CMP #$7E/$7F`` or ``BMI/BPL``).
    """
    jump = _op(image, f"B9 {_w(wave_ctrl)} 9D {_w(wtptr)} A8 B9 {{op:w}}")
    if jump is not None:
        return jump
    for m in find_code_all(image, f"BC {_w(wtptr)} B9 {{op:w}} ??"):
        note = m.captures["op"]
        if note != wave_ctrl and m.u8(6) in (0xC9, 0x30, 0x10):
            return note
    return None


def _find_wave_columns(image: bytes) -> Tuple[Optional[int], Optional[int]]:
    """Discover the ``(note, ctrl)`` wavetable columns, or ``(None, None)``.

    Anchors on the CTRL-shadow cell, then the wavetable store frame
    ``LDY wtptr,X ; LDA ctrl,Y ; STA shadow,X`` -- confirmed by an ``INC
    wtptr,X`` tick (the per-frame wavetable advance) so a per-instrument CTRL
    field group (V1/V2) is rejected -- then the parallel note column.
    """
    shadow = _find_ctrl_shadow(image)
    if shadow is None:
        return None, None
    for m in find_code_all(image, f"BC {{wtptr:w}} B9 {{ctrl:w}} 9D {_w(shadow)}"):
        wtptr = m.captures["wtptr"]
        wave_ctrl = m.captures["ctrl"]
        if bytes([0xFE, wtptr & 0xFF, wtptr >> 8]) not in image:  # INC wtptr,X
            continue
        note = _find_note_column(image, wave_ctrl, wtptr)
        if note is not None:
            return note, wave_ctrl
    return None, None


def _find_interleaved_wave(image: bytes) -> Optional[int]:
    """Base of the V1/V2 single interleaved ``(ctrl, note)`` wave stream, or ``None``.

    V1/V2 predate the two parallel wave columns: one stream holds ``ctrl`` (even
    byte, written to the CTRL shadow) and ``note`` (odd byte) alternating, with a
    cursor stepped ``+2`` per tick.  A ``$FF`` ctrl byte is a **restart** whose
    loop target is reloaded from a per-voice runtime cell seeded at instrument
    init from the instrument's wave-start field -- not an inline column.  Anchored
    on the restart handler ``CMP #$FF ; BNE +9 ; LDA restart,X ; STA cursor,X ;
    JMP`` cross-checked against the stream read ``LDY cursor,X ; LDA base,Y ;
    CMP #$FF`` with the same cursor cell.  Returns the stream base.

    This model is *not* invertible to the editor's two 256-byte columns for a
    faithful export: the ~16-byte instrument records and the embedded limit-based
    PW/filter have no editor representation (see ``docs/versions.md``); the base
    is returned only so the recovered model can describe itself honestly.
    """
    for m in find_code_all(image, "C9 FF D0 09 BD ?? ?? 9D {cursor:w} 4C"):
        cursor = m.captures["cursor"]
        read = _op(image, f"BC {_w(cursor)} B9 {{op:w}} C9 FF")
        if read is not None:
            return read
    return None


def _find_cmdparam(image: bytes) -> Optional[int]:
    """Command-parameter table via ``ASL ; TAY ; LDA param,Y ; PHA``."""
    return _op(image, "0A A8 B9 {op:w} 48")


def _find_filterprog(image: bytes) -> Optional[int]:
    """Filter/groove program via ``LDY grvidx ; LDA filterprog,Y ; STA grvctr``."""
    return _op(image, "AC ?? ?? B9 {op:w} 8D")


def _find_pwprog(image: bytes) -> Optional[int]:
    """PW program via ``LDY pwcur,X ; LDA pwnext,Y ; STA pwcur,X`` (base = next-3).

    The ``LDY`` and ``STA`` cursor operands must be the same cell (the base is
    read and written back), so the two captured words are required to agree.
    """
    for m in find_code_all(image, "BC {cur:w} B9 {op:w} 9D {cur2:w}"):
        if m.captures["cur"] == m.captures["cur2"]:
            return m.captures["op"] - 3
    return None


def _find_pitch_lookup(image: bytes) -> Optional[int]:
    """V17 interleaved pitch read ``TAY ; LDA base,Y ; STA ; LDA base+1,Y``.

    V17 lacks V20's ``LDA pitch+1,Y ; ADC #$00`` high-byte-carry idiom; it reads
    the 16-bit pitch table's high byte directly from ``base+1`` after ``TAY``
    (the freshly computed note*2+transpose index).  Returns the lo-column base.
    """
    for m in find_code_all(image, "A8 B9 {base:w}"):
        base = m.captures["base"]
        nxt = bytes([0xB9, (base + 1) & 0xFF, ((base + 1) >> 8) & 0xFF])
        for gap in (6, 7):  # a 2- or 3-byte store between the two column reads
            if m.buf[m.addr + gap : m.addr + gap + 3] == nxt:
                return base
    return None


def _pitch_coherent(image: bytes, load: int, base: int) -> bool:
    """A note->freq table is 16-bit-LE, positive, and monotonic non-decreasing."""
    off = base - load
    span = min(96, (len(image) - off) // 2)
    if span < 64:
        return False
    vals = [image[off + 2 * i] | (image[off + 2 * i + 1] << 8) for i in range(span)]
    return vals[0] > 0 and all(vals[i] <= vals[i + 1] for i in range(span - 1))


@dataclass
class NewPlayerModel:
    """Recovered song model of a JCH NewPlayer wavetable-family tune.

    Holds the container entry points, the discovered (relocated) table base
    addresses, and the raw image the order lists / patterns / instruments are
    read out of.  ``version`` is the best-effort family tag (e.g. ``"V20"``)
    or ``"JCH_NewPlayer"`` when the exact sub-version is not fingerprinted.

    This is the recovered *structure*; pyjch does not replay these versions
    byte-exactly (see the module docstring).
    """

    # pylint: disable=too-many-instance-attributes  # a full tune is wide
    load_addr: int
    init_addr: int
    play_addr: int
    name: str = ""
    author: str = ""
    released: str = ""
    version: str = "JCH_NewPlayer"
    num_subtunes: int = 1
    # Discovered (relocated) table base addresses.
    subtune_table: int = 0
    patternptr_lo: int = 0
    patternptr_hi: int = 0
    instruments: int = 0
    wave_note_col: Optional[int] = None
    wave_ctrl_col: Optional[int] = None
    wave_stream: Optional[int] = None  # V1/V2 single interleaved (ctrl,note) stream
    pitch_table: Optional[int] = None
    cmdparam: Optional[int] = None
    filterprog: Optional[int] = None
    pwprog: Optional[int] = None
    image: bytes = b""
    container_header: bytes = b""

    def _byte(self, abs_addr: int) -> Optional[int]:
        idx = abs_addr - self.load_addr
        if 0 <= idx < len(self.image):
            return self.image[idx]
        return None

    def _word(self, abs_addr: int) -> Optional[int]:
        lo = self._byte(abs_addr)
        hi = self._byte(abs_addr + 1)
        if lo is None or hi is None:
            return None
        return lo | (hi << 8)

    def orderlist_ptr(self, subtune: int, voice: int) -> Optional[int]:
        """Absolute address of ``voice``'s order list for ``subtune``."""
        return self._word(self.subtune_table + subtune * _SUBTUNE_RECORD + voice * 2)

    def subtune_tempo(self, subtune: int) -> Optional[int]:
        """The subtune's tempo/groove reload byte (record offset +6)."""
        return self._byte(self.subtune_table + subtune * _SUBTUNE_RECORD + 6)

    def pattern_ptr(self, index: int) -> Optional[int]:
        """Absolute base address of pattern ``index``."""
        lo = self._byte(self.patternptr_lo + index)
        hi = self._byte(self.patternptr_hi + index)
        if lo is None or hi is None:
            return None
        return lo | (hi << 8)

    def instrument(self, index: int) -> Optional[List[int]]:
        """The 8-byte instrument record ``index`` (AD,SR,wavspd,filt,...)."""
        base = self.instruments + index * 8
        rec = [self._byte(base + n) for n in range(8)]
        if any(b is None for b in rec):
            return None
        return rec

    def iter_orderlist(self, subtune: int, voice: int) -> List[int]:
        """Pattern indices referenced by ``voice``'s order list for ``subtune``.

        Walks the order-list stream (``$80+`` = transpose prefix, ``$FE`` =
        stop, ``$FF`` = loop) until a terminator or the walk cap, returning
        the pattern indices in order.  Raises :class:`SidParseError` if the
        stream runs off the image or never terminates -- the signal an
        order-list base is not actually an order list.
        """
        addr = self.orderlist_ptr(subtune, voice)
        if addr is None:
            raise SidParseError("order-list pointer out of range")
        end = self.load_addr + len(self.image)
        indices: List[int] = []
        steps = 0
        while True:
            if steps >= _MAX_ORDER_WALK:
                raise SidParseError("order list does not terminate")
            steps += 1
            byte = self._byte(addr)
            if byte is None:
                raise SidParseError("order list runs off image")
            if byte in (_ORD_STOP, _ORD_LOOP):
                return indices
            addr += 1
            if byte >= 0x80:  # transpose prefix; next byte is the pattern index
                continue
            if not self.load_addr <= (self.pattern_ptr(byte) or -1) < end:
                raise SidParseError(f"pattern index {byte} points out of range")
            indices.append(byte)


def _entry_vectors_ok(load: int, image: bytes, init: int, play: int) -> bool:
    """``init`` and ``play`` are ``JMP`` vectors into the image.

    The family's entry points are a ``JMP init ; JMP play`` pair; the pair sits
    at offset ``init - load`` (not necessarily image start -- e.g. tunes that
    load at ``$0F00`` with ``init=$1000`` carry a leading block).
    """
    end = load + len(image)
    for vec in (init, play):
        off = vec - load
        if not 0 <= off < len(image) - 2 or image[off] != 0x4C:
            return False
        target = image[off + 1] | (image[off + 2] << 8)
        if not load <= target < end:
            return False
    return True


def discover(load: int, image: bytes, init: int, play: int) -> Optional[dict]:
    """Discover the family table bases in ``image``, or ``None`` if absent.

    Returns a dict of base addresses when the four *required* idioms (subtune
    table, pattern-pointer low/high, instrument records) are all present and
    in range; the optional wavetable/pitch bases are included when found.
    """
    if not _entry_vectors_ok(load, image, init, play):
        return None
    end = load + len(image)
    bases = {
        "subtune_table": _find_subtune_base(image),
        "patternptr_lo": _op(image, "B9 {op:w} 85 FB"),  # LDA lo,Y ; STA $FB
        "patternptr_hi": _op(image, "B9 {op:w} 85 FC"),  # LDA hi,Y ; STA $FC
        "instruments": _find_instrument_base(image),
    }
    for addr in bases.values():
        if addr is None or not load <= addr < end:
            return None
    note, ctrl = _find_wave_columns(image)
    if (
        note is not None
        and ctrl is not None
        and load <= note < end
        and load <= ctrl < end
    ):
        bases["wave_note_col"] = note
        bases["wave_ctrl_col"] = ctrl
    else:
        wave = _op(image, "B9 {op:w} C9 7E")  # LDA note,Y ; CMP #$7E (hold test)
        if wave is not None and load <= wave < end:
            bases["wave_note_col"] = wave
        stream = _find_interleaved_wave(image)
        if stream is not None and load <= stream < end:
            bases["wave_stream"] = stream
    pitch_hi = _op(image, "B9 {op:w} 69 00")  # LDA pitch+1,Y ; ADC #$00 (carry)
    if pitch_hi is not None and load < pitch_hi < end:
        bases["pitch_table"] = pitch_hi - 1
    else:  # V17: interleaved read, no ADC-carry high byte
        pitch = _find_pitch_lookup(image)
        if (
            pitch is not None
            and load <= pitch < end
            and _pitch_coherent(image, load, pitch)
        ):
            bases["pitch_table"] = pitch
    for key, addr in (
        ("cmdparam", _find_cmdparam(image)),
        ("filterprog", _find_filterprog(image)),
        ("pwprog", _find_pwprog(image)),
    ):
        if addr is not None and load <= addr < end:
            bases[key] = addr
    return bases


def recover(  # pylint: disable=too-many-arguments,too-many-locals
    load: int,
    init: int,
    play: int,
    name: str,
    author: str,
    released: str,
    image: bytes,
    header: bytes,
    version: str = "JCH_NewPlayer",
    num_subtunes: int = 1,
) -> NewPlayerModel:
    """Recover a coherent :class:`NewPlayerModel`, or raise ``SidParseError``.

    Discovers the family table bases and then validates the song is coherent:
    every subtune's three order-list pointers are in range, and each subtune's
    order lists walk to a terminator through in-range pattern pointers.  A
    tune whose discovered bases do not form a coherent song is rejected rather
    than returned as garbage.
    """
    bases = discover(load, image, init, play)
    if bases is None:
        raise SidParseError(
            "not a recoverable JCH NewPlayer family layout: "
            "required table-discovery idioms absent or out of range"
        )
    model = NewPlayerModel(
        load_addr=load,
        init_addr=init,
        play_addr=play,
        name=name,
        author=author,
        released=released,
        version=version,
        num_subtunes=max(1, num_subtunes),
        image=image,
        container_header=header,
        **bases,
    )
    end = load + len(image)
    total_patterns = 0
    for subtune in range(model.num_subtunes):
        ptrs = [model.orderlist_ptr(subtune, v) for v in range(3)]
        if any(p is None or not load <= p < end for p in ptrs):
            raise SidParseError(f"subtune {subtune} order-list pointers out of range")
        if model.subtune_tempo(subtune) is None:
            raise SidParseError(f"subtune {subtune} tempo byte out of range")
        for voice in range(3):
            total_patterns += len(model.iter_orderlist(subtune, voice))
    if total_patterns == 0:
        raise SidParseError("no pattern references found: not a coherent song")
    if model.instrument(0) is None:
        raise SidParseError("instrument 0 record out of range")
    return model


def recognize(load: int, image: bytes, init: int, play: int) -> Optional[int]:
    """Return the subtune-table anchor if ``image`` is a coherent family tune.

    A cheap gate for :meth:`~pyjch.reader.JchSidParser.recognize`: it runs the
    full :func:`recover` coherence check (order lists terminate through in-range
    pattern pointers) and returns the discovered subtune-table base, or ``None``
    when the image is not a recoverable NewPlayer family layout.
    """
    try:
        model = recover(load, init, play, "", "", "", image, b"")
    except SidParseError:
        return None
    return model.subtune_table


# ---- best-effort prologue classes (play-routine prologue, masked) -----------
# Masked ``CodePattern`` skeletons of the opcode run at the play routine that
# cluster the common family builds; address operands are wildcarded (``??``).
# Derived by byte analysis of HVSC representatives (not copied from any signature
# database), and used only to *label* a recovered model, never to gate recovery.
# A play prologue does not map 1:1 to a sidid ``Vnn`` sub-version -- several sidid
# versions share one -- so these are honest prologue-class tags, not exact version
# numbers (use ``sidid`` for the precise sub-version).  V20's is distinctive.
_PROLOGUE_CLASSES: Tuple[Tuple[str, CodePattern], ...] = tuple(
    (tag, CodePattern(spec))
    for tag, spec in (
        ("V20", "A5 FB 48 A5 FC 48 CE ?? ?? 10 1D AD"),
        ("V9-class", "A5 FB 48 A5 FC 48 A2 02 CE ?? ?? 10"),
        ("V13/V14-class", "A2 02 BD ?? ?? C9 02 D0 ?? BC ?? ??"),
        ("V11/V17-class", "A5 FB 48 A5 FC 48 A2 02 BD 06 10 D0"),
        ("V15/V18-class", "A2 02 A5 FB 48 A5 FC 48 BD 06 10"),
    )
)


def classify_version(load: int, image: bytes, play: int) -> str:
    """Best-effort prologue-class tag for ``image`` (else ``JCH_NewPlayer``).

    Matches each masked prologue skeleton at the play routine (offset
    ``play - load``); the tag is a play-prologue class, not an exact sidid
    sub-version.
    """
    play_off = play - load
    if not 0 <= play_off < len(image):
        return "JCH_NewPlayer"
    for tag, pattern in _PROLOGUE_CLASSES:
        if find_code_first(
            image, pattern, start=play_off, end=play_off + pattern.length
        ):
            return tag
    return "JCH_NewPlayer"
