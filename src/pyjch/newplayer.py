"""Version-aware reader for the JCH *NewPlayer wavetable family*.

pyjch's native :mod:`~pyjch.player` models two specific routines (the
canonical ``JCH_NewPlayer_V0x`` layout and the V20 build).  HVSC, however,
holds thousands of tunes built by the *later* JCH NewPlayer engine -- the two-column wavetable
player fully reverse-engineered in
``re-trackers/JCH_NewPlayer/{jch-architecture.md,jch-player.asm}`` (that
disassembly is a ``JCH_NewPlayer_V20`` tune, the largest HVSC bucket).

The V20 build of this family is replayed byte-exactly by the native engine in
:mod:`~pyjch.player`; every other recovered version plays byte-exactly too, by
running the tune's own 6502 driver on jennings via
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

from dataclasses import dataclass, field
from functools import cached_property, lru_cache
from typing import Dict, List, Optional, Tuple

from pysidtracker import (
    CodePattern,
    SidImage,
    SiteMatch,
    SitePattern,
    code_match_at,
    follow_jmp,
    is_jmp_vector,
)

from pyjch.errors import SidParseError

# Order-list control bytes.
_ORD_STOP = 0xFE
_ORD_LOOP = 0xFF

_SUBTUNE_RECORD = 8  # bytes per subtune record {v0lo,hi,v1lo,hi,v2lo,hi,tempo,?}
_MAX_ORDER_WALK = 512  # order-list length cap for the coherence walk

# Offset of the ``ACTIVE`` work cell from the code base (the init address); the
# play-routine prologues read it, so their operand is built per tune.
ACTIVE = 0x006

# Facet values: the wave-table layouts, pitch-table forms and CTRL-shadow forms a
# family build can take.
WAVE_COLUMNS = "columns"
WAVE_NOTE_ONLY = "note-only"
WAVE_STREAM = "stream"
WAVE_NONE = "none"
PITCH_ADC_CARRY = "adc-carry"
PITCH_INTERLEAVED = "interleaved"
PITCH_NONE = "none"
SHADOW_V13 = "v13"
SHADOW_V9 = "v9"
SHADOW_NONE = "none"

# A recovered form: its facet value and the code address it was read at.
Form = Tuple[str, Optional[int]]


def image_of(load: int, image: bytes) -> SidImage:
    """A bare :class:`~pysidtracker.SidImage` of ``image`` loaded at ``load``."""
    return SidImage.from_prg(load.to_bytes(2, "little") + image)


@lru_cache(maxsize=512)
def _site(spec: str, first: bool) -> SitePattern:
    return SitePattern(spec, first=first)


def find_site(img: SidImage, spec: str) -> Optional[SiteMatch]:
    """The lowest-address match of site ``spec`` in the loaded code, or ``None``."""
    return _site(spec, True).find_first(img)


def find_sites(img: SidImage, spec: str) -> List[SiteMatch]:
    """Every match of site ``spec`` in the loaded code, ascending."""
    return _site(spec, False).find_all(img)


def find_operand(img: SidImage, spec: str) -> Optional[int]:
    """First ``{op}`` operand captured by site ``spec`` in ``img``, or ``None``.

    ``spec`` is a :class:`~pysidtracker.SitePattern` string: fixed opcodes plus
    ``{op:w}`` for the relocated table base baked into the instruction operand.
    Each engine idiom is one such masked code fragment.
    """
    match = find_site(img, spec)
    return None if match is None else match.captures["op"]


def abs_tokens(value: int) -> str:
    """A 16-bit value as two little-endian pattern literal tokens."""
    return f"{value & 0xFF:02X} {(value >> 8) & 0xFF:02X}"


_INSTRUMENT = "B9 {op:w} BC ?? ?? 99 05 D4"  # LDA inst,Y ; LDY stride,X ; STA $D405,Y
_SUBTUNE = "A2 00 B9 {op:w} 9D"  # LDX #$00 ; LDA subtune,Y ; STA abs,X
# CTRL-shadow cell from the blit ``LDA shadow,X .. AND gate,X ; STA $D404,Y``: the
# per-voice CTRL byte ANDed with the gate mask and written every frame.  ``LDA
# shadow,X`` (``BD``) sits immediately before ``AND gate,X`` (V13+ builds) or one
# ``LDY stride,X`` (``BC``) earlier (V9-11 builds).
_CTRL_SHADOW = (
    "( v13: BD {op:w} 3D ?? ?? 99 04 D4 " "| v9: BD {op:w} BC ?? ?? 3D ?? ?? 99 04 D4 )"
)
_CMDPARAM = "0A A8 B9 {op:w} 48"  # ASL ; TAY ; LDA param,Y ; PHA
_FILTERPROG = "AC ?? ?? B9 {op:w} 8D"  # LDY grvidx ; LDA filterprog,Y ; STA grvctr
# LDY pwcur,X ; LDA pwnext,Y ; STA pwcur,X: the cursor cell is read and written back.
_PWPROG = "BC {cur:w} B9 {op:w} 9D {cur:w}"
_PITCH_CARRY = "B9 {op:w} 69 00"  # LDA pitch+1,Y ; ADC #$00 (carry)
_NOTE_HOLD = "B9 {op:w} C9 7E"  # LDA note,Y ; CMP #$7E (hold test)
_HARD_RESTART = "A9 {op} 99 04 D4"  # LDA #imm ; STA $D404,Y


def hard_restart_site(img: SidImage) -> Optional[SiteMatch]:
    """The hard-restart CTRL immediate site ``LDA #imm ; STA $D404,Y``, or ``None``."""
    return find_site(img, _HARD_RESTART)


def find_ctrl_shadow(img: SidImage) -> Optional[Tuple[str, SiteMatch]]:
    """``(form, site)`` of the CTRL-shadow blit, preferring the V13+ form."""
    hits = find_sites(img, _CTRL_SHADOW)
    for label, form in (("v13", SHADOW_V13), ("v9", SHADOW_V9)):
        for hit in hits:
            if hit.alt == label:
                return form, hit
    return None


def _find_note_column(img: SidImage, wave_ctrl: int, wtptr: int) -> Optional[int]:
    """Parallel note column paired with ``wave_ctrl`` under pointer ``wtptr``.

    Prefers the ``$7F``-jump handler ``LDA ctrl,Y ; STA wtptr,X ; TAY ;
    LDA note,Y`` (which ties both columns to the same pointer); falls back to
    the note-column read ``LDY wtptr,X ; LDA note,Y`` gated by its column test
    (``CMP #$7E/$7F`` or ``BMI/BPL``).
    """
    jump = find_operand(
        img, f"B9 {abs_tokens(wave_ctrl)} 9D {abs_tokens(wtptr)} A8 B9 {{op:w}}"
    )
    if jump is not None:
        return jump
    for m in find_sites(img, f"BC {abs_tokens(wtptr)} B9 {{op:w}} ( C9 | 30 | 10 )"):
        if m.captures["op"] != wave_ctrl:
            return m.captures["op"]
    return None


def _find_wave_columns(
    img: SidImage,
) -> Tuple[Optional[int], Optional[int], Optional[int], Optional[Form]]:
    """Discover the ``(note, ctrl, site, shadow form)`` wavetable columns.

    Anchors on the CTRL-shadow cell, then the wavetable store frame
    ``LDY wtptr,X ; LDA ctrl,Y ; STA shadow,X`` -- confirmed by an ``INC
    wtptr,X`` tick (the per-frame wavetable advance) so a per-instrument CTRL
    field group (V1/V2) is rejected -- then the parallel note column.  ``site``
    is the store frame's address; the shadow form is reported even when the
    columns are not found.
    """
    shadow = find_ctrl_shadow(img)
    if shadow is None:
        return None, None, None, None
    form, site = shadow
    shadow_form: Form = (form, site.addr)
    for m in find_sites(
        img, f"BC {{wtptr:w}} B9 {{ctrl:w}} 9D {abs_tokens(site.captures['op'])}"
    ):
        wtptr = m.captures["wtptr"]
        wave_ctrl = m.captures["ctrl"]
        if bytes([0xFE, wtptr & 0xFF, wtptr >> 8]) not in img.image:  # INC wtptr,X
            continue
        note = _find_note_column(img, wave_ctrl, wtptr)
        if note is not None:
            return note, wave_ctrl, m.addr, shadow_form
    return None, None, None, shadow_form


def _find_interleaved_wave(img: SidImage) -> Optional[SiteMatch]:
    """Site of the V1/V2 single interleaved ``(ctrl, note)`` wave stream, or ``None``.

    V1/V2 predate the two parallel wave columns: one stream holds ``ctrl`` (even
    byte, written to the CTRL shadow) and ``note`` (odd byte) alternating, with a
    cursor stepped ``+2`` per tick.  A ``$FF`` ctrl byte is a **restart** whose
    loop target is reloaded from a per-voice runtime cell seeded at instrument
    init from the instrument's wave-start field -- not an inline column.  Anchored
    on the restart handler ``CMP #$FF ; BNE +9 ; LDA restart,X ; STA cursor,X ;
    JMP`` cross-checked against the stream read ``LDY cursor,X ; LDA base,Y ;
    CMP #$FF`` with the same cursor cell.  The match carries the stream base as
    ``op``.

    This model is *not* invertible to the editor's two 256-byte columns for a
    faithful export: the ~16-byte instrument records and the embedded limit-based
    PW/filter have no editor representation (see ``docs/versions.md``); the base
    is returned only so the recovered model can describe itself honestly.
    """
    for m in find_sites(img, "C9 FF D0 09 BD ?? ?? 9D {cursor:w} 4C"):
        read = find_site(
            img, f"BC {abs_tokens(m.captures['cursor'])} B9 {{op:w}} C9 FF"
        )
        if read is not None:
            return read
    return None


def _find_pitch_lookup(img: SidImage) -> Optional[SiteMatch]:
    """V17 interleaved pitch read ``TAY ; LDA base,Y ; STA ; LDA base+1,Y``.

    V17 lacks V20's ``LDA pitch+1,Y ; ADC #$00`` high-byte-carry idiom; it reads
    the 16-bit pitch table's high byte directly from ``base+1`` after ``TAY``
    (the freshly computed note*2+transpose index).  The match carries the lo-column
    base as ``base``.
    """
    for m in find_sites(img, "A8 B9 {base:w}"):
        base = m.captures["base"]
        nxt = bytes([0xB9, (base + 1) & 0xFF, ((base + 1) >> 8) & 0xFF])
        for gap in (6, 7):  # a 2- or 3-byte store between the two column reads
            if m.buf[m.addr + gap : m.addr + gap + 3] == nxt:
                return m
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

    This is the recovered *structure*; non-V20 versions replay byte-exactly via
    :class:`~pysidtracker.EmuPlayer` (see the module docstring).
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
    # The 0-based subtune this model plays by default (``parse(..., subtune=k)``).
    subtune: int = 0
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
    # How each discovery idiom was realised: ``{facet: (value, code address)}``
    # (``wave``, ``pitch``, ``ctrl_shadow``); read by :mod:`pyjch.variant`.
    forms: Dict[str, Form] = field(default_factory=dict)

    @cached_property
    def sid(self) -> SidImage:
        """The loaded image as a :class:`~pysidtracker.SidImage`."""
        return image_of(self.load_addr, self.image)

    def _byte(self, abs_addr: int) -> Optional[int]:
        return self.sid.byte(abs_addr) if self.sid.contains(abs_addr) else None

    def _word(self, abs_addr: int) -> Optional[int]:
        if not (self.sid.contains(abs_addr) and self.sid.contains(abs_addr + 1)):
            return None
        return self.sid.word(abs_addr)

    def orderlist_ptr(self, subtune: int, voice: int) -> Optional[int]:
        """Absolute address of ``voice``'s order list for ``subtune``."""
        return self._word(self.subtune_table + subtune * _SUBTUNE_RECORD + voice * 2)

    def subtune_tempo(self, subtune: int) -> Optional[int]:
        """The subtune's tempo/groove reload byte (record offset +6)."""
        return self._byte(self.subtune_table + subtune * _SUBTUNE_RECORD + 6)

    def pattern_ptr(self, index: int) -> Optional[int]:
        """Absolute base address of pattern ``index``."""
        if not (
            self.sid.contains(self.patternptr_lo + index)
            and self.sid.contains(self.patternptr_hi + index)
        ):
            return None
        return self.sid.ptr(self.patternptr_lo, self.patternptr_hi, index)

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


def _entry_vectors_ok(img: SidImage, init: int, play: int) -> bool:
    """``init`` and ``play`` are ``JMP`` vectors into the image.

    The family's entry points are a ``JMP init ; JMP play`` pair; the pair sits
    at offset ``init - load`` (not necessarily image start -- e.g. tunes that
    load at ``$0F00`` with ``init=$1000`` carry a leading block).
    """
    return is_jmp_vector(img, init) and is_jmp_vector(img, play)


def _discover(img: SidImage, init: int, play: int):
    """``(bases, forms)`` of the family tables in ``img``, or ``None`` if absent."""
    if not _entry_vectors_ok(img, init, play):
        return None
    bases = {
        "subtune_table": find_operand(img, _SUBTUNE),
        "patternptr_lo": find_operand(img, "B9 {op:w} 85 FB"),  # LDA lo,Y ; STA $FB
        "patternptr_hi": find_operand(img, "B9 {op:w} 85 FC"),  # LDA hi,Y ; STA $FC
        "instruments": find_operand(img, _INSTRUMENT),
    }
    for addr in bases.values():
        if addr is None or not img.contains(addr):
            return None
    forms: Dict[str, Form] = {}
    note, ctrl, site, shadow = _find_wave_columns(img)
    if shadow is not None:
        forms["ctrl_shadow"] = shadow
    if (
        note is not None
        and ctrl is not None
        and img.contains(note)
        and img.contains(ctrl)
    ):
        bases["wave_note_col"] = note
        bases["wave_ctrl_col"] = ctrl
        forms["wave"] = (WAVE_COLUMNS, site)
    else:
        hold = find_site(img, _NOTE_HOLD)
        if hold is not None and img.contains(hold.captures["op"]):
            bases["wave_note_col"] = hold.captures["op"]
            forms["wave"] = (WAVE_NOTE_ONLY, hold.addr)
        stream = _find_interleaved_wave(img)
        if stream is not None and img.contains(stream.captures["op"]):
            bases["wave_stream"] = stream.captures["op"]
            forms.setdefault("wave", (WAVE_STREAM, stream.addr))
    carry = find_site(img, _PITCH_CARRY)
    if carry is not None and img.load < carry.captures["op"] < img.end:
        bases["pitch_table"] = carry.captures["op"] - 1
        forms["pitch"] = (PITCH_ADC_CARRY, carry.addr)
    else:  # V17: interleaved read, no ADC-carry high byte
        lookup = _find_pitch_lookup(img)
        if (
            lookup is not None
            and img.contains(lookup.captures["base"])
            and _pitch_coherent(img.image, img.load, lookup.captures["base"])
        ):
            bases["pitch_table"] = lookup.captures["base"]
            forms["pitch"] = (PITCH_INTERLEAVED, lookup.addr)
    pwprog = find_site(img, _PWPROG)
    for key, addr in (
        ("cmdparam", find_operand(img, _CMDPARAM)),
        ("filterprog", find_operand(img, _FILTERPROG)),
        ("pwprog", None if pwprog is None else pwprog.captures["op"] - 3),
    ):
        if addr is not None and img.contains(addr):
            bases[key] = addr
    return bases, forms


def discover(load: int, image: bytes, init: int, play: int) -> Optional[dict]:
    """Discover the family table bases in ``image``, or ``None`` if absent.

    Returns a dict of base addresses when the four *required* idioms (subtune
    table, pattern-pointer low/high, instrument records) are all present and
    in range; the optional wavetable/pitch bases are included when found.
    """
    found = _discover(image_of(load, image), init, play)
    return None if found is None else found[0]


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
    found = _discover(image_of(load, image), init, play)
    if found is None:
        raise SidParseError(
            "not a recoverable JCH NewPlayer family layout: "
            "required table-discovery idioms absent or out of range"
        )
    bases, forms = found
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
        forms=forms,
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
# Masked skeletons of the opcode run at the play routine that cluster the common
# family builds; address operands are wildcarded (``??``) except the ``ACTIVE``
# work cell, whose address is built from the tune's code base (``init``) so a
# relocated build matches too.  Derived by byte analysis of HVSC representatives
# (not copied from any signature database), and used only to *label* a recovered
# model, never to gate recovery.  A play prologue does not map 1:1 to a sidid
# ``Vnn`` sub-version -- several sidid versions share one -- so these are honest
# prologue-class tags, not exact version numbers (use ``sidid`` for the precise
# sub-version).  V20's is distinctive.
_PROLOGUE_CLASSES: Tuple[Tuple[str, str], ...] = (
    ("V20", "A5 FB 48 A5 FC 48 CE ?? ?? 10 1D AD"),
    ("V9-class", "A5 FB 48 A5 FC 48 A2 02 CE ?? ?? 10"),
    ("V13/V14-class", "A2 02 BD ?? ?? C9 02 D0 ?? BC ?? ??"),
    ("V11/V17-class", "A5 FB 48 A5 FC 48 A2 02 BD {active} D0"),
    ("V15/V18-class", "A2 02 A5 FB 48 A5 FC 48 BD {active}"),
)

DEFAULT_VERSION = "JCH_NewPlayer"


@lru_cache(maxsize=512)
def _prologue_patterns(active: int) -> Tuple[Tuple[str, CodePattern], ...]:
    cell = abs_tokens(active)
    return tuple(
        (tag, CodePattern(spec.format(active=cell))) for tag, spec in _PROLOGUE_CLASSES
    )


def prologue_class(img: SidImage, init: int, play: int) -> Optional[Tuple[str, int]]:
    """``(tag, address)`` of the prologue class matched at ``play``, or ``None``.

    Matches each masked prologue skeleton at the play routine, following a
    ``JMP abs`` play vector; the tag is a play-prologue class, not an exact
    sidid sub-version.
    """
    start = follow_jmp(img, play)
    for tag, pattern in _prologue_patterns((init + ACTIVE) & 0xFFFF):
        if code_match_at(img, pattern, start) is not None:
            return tag, start
    return None


def classify_version(
    load: int, image: bytes, play: int, init: Optional[int] = None
) -> str:
    """Best-effort prologue-class tag for ``image`` (else ``JCH_NewPlayer``).

    ``init`` is the code base (default: the load address) the ``ACTIVE`` work
    cell is relative to.
    """
    found = prologue_class(image_of(load, image), load if init is None else init, play)
    return DEFAULT_VERSION if found is None else found[0]
