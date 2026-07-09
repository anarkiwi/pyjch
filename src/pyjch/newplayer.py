"""Version-aware reader for the JCH *NewPlayer wavetable family*.

pyjch's byte-exact :mod:`~pyjch.player` models one specific routine (the
canonical ``JCH_NewPlayer_V0x`` layout).  HVSC, however, holds thousands of
tunes built by the *later* JCH NewPlayer engine -- the two-column wavetable
player fully reverse-engineered in
``re-trackers/JCH_NewPlayer/{jch-architecture.md,jch-player.asm}`` (that
disassembly is a ``JCH_NewPlayer_V20`` tune, the largest HVSC bucket).

Those versions run a *different player opcode stream*, so pyjch cannot yet
replay them byte-exactly.  Their **song DATA**, though, is laid out the same
way across the whole family -- only relocated -- and is recoverable directly
from the loaded image:

* a **subtune table** ``{orderptr v0,v1,v2, tempo, ...}`` per subtune,
* per-voice **order lists** (pattern-index streams, ``$FE`` stop / ``$FF``
  loop / ``$80+`` transpose),
* **pattern pointer** low/high tables (pattern base = ``hi<<8 | lo``),
* 8-byte **instrument** records,
* the note-frequency **pitch table** and the **wavetable note column**
  (present on some versions).

Each table base is discovered by its surrounding player-code instruction
idiom (the same relocation-safe idiom search the V0x reader uses), not a
fixed offset.  :func:`recover` returns a :class:`NewPlayerModel` only when
the discovered tables form a **coherent** song (bases in range, order lists
terminate within the image, pattern pointers in range); otherwise it raises,
so it never emits garbage from a foreign player.

This is a *reader*, not a player: a recovered model is the recovered song
structure, not a byte-exact-verified replay.  See ``docs/versions.md``.
"""

from dataclasses import dataclass
from typing import List, Optional, Tuple

from pyjch.errors import SidParseError

# Discovery idioms (opcodes fixed by the engine; address operands vary per
# build, so we read the operand and keep the opcode frame as the anchor).
_LDA_ABSY = b"\xb9"  # LDA abs,Y
_STA_FB = b"\x85\xfb"  # STA $FB   -> pattern-pointer low table feeds zp ptr
_STA_FC = b"\x85\xfc"  # STA $FC   -> pattern-pointer high table
_CMP_7E = b"\xc9\x7e"  # CMP #$7E  -> wavetable note-column hold test
_ADC_00 = b"\x69\x00"  # ADC #$00  -> pitch-table high byte carry
_STA_D405Y = b"\x99\x05\xd4"  # STA $D405,Y -> AD from instrument record
_SUBTUNE_PREFIX = b"\xa2\x00\xb9"  # LDX #$00 ; LDA subtune,Y

# Order-list control bytes.
_ORD_STOP = 0xFE
_ORD_LOOP = 0xFF

_SUBTUNE_RECORD = 8  # bytes per subtune record {v0lo,hi,v1lo,hi,v2lo,hi,tempo,?}
_MAX_ORDER_WALK = 512  # order-list length cap for the coherence walk


def _find_absy(image: bytes, suffix: bytes) -> Optional[int]:
    """First ``LDA abs,Y <suffix>`` operand, or ``None``."""
    start = 0
    while True:
        hit = image.find(_LDA_ABSY, start)
        if hit < 0:
            return None
        start = hit + 1
        op = hit + 1
        if op + 2 + len(suffix) > len(image):
            continue
        if image[op + 2 : op + 2 + len(suffix)] == suffix:
            return image[op] | (image[op + 1] << 8)


def _find_instrument_base(image: bytes) -> Optional[int]:
    """``LDA inst,Y ; LDY $1740,X ; STA $D405,Y`` -> instrument-record base."""
    limit = len(image) - 9
    for i in range(max(0, limit) + 1):
        if (
            image[i] == 0xB9
            and image[i + 3] == 0xBC
            and image[i + 6 : i + 9] == _STA_D405Y
        ):
            return image[i + 1] | (image[i + 2] << 8)
    return None


def _find_subtune_base(image: bytes) -> Optional[int]:
    """``LDX #$00 ; LDA subtune,Y ; STA abs,X`` -> subtune-table base."""
    start = 0
    while True:
        hit = image.find(_SUBTUNE_PREFIX, start)
        if hit < 0:
            return None
        start = hit + 1
        op = hit + len(_SUBTUNE_PREFIX)
        if op + 3 > len(image):
            continue
        if image[op + 2] == 0x9D:  # STA abs,X frames the copy loop
            return image[op] | (image[op + 1] << 8)


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
    pitch_table: Optional[int] = None
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
        "patternptr_lo": _find_absy(image, _STA_FB),
        "patternptr_hi": _find_absy(image, _STA_FC),
        "instruments": _find_instrument_base(image),
    }
    for addr in bases.values():
        if addr is None or not load <= addr < end:
            return None
    wave = _find_absy(image, _CMP_7E)
    if wave is not None and load <= wave < end:
        bases["wave_note_col"] = wave
    pitch_hi = _find_absy(image, _ADC_00)
    if pitch_hi is not None and load < pitch_hi < end:
        bases["pitch_table"] = pitch_hi - 1
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
# Opcode runs at the play routine that cluster the common family builds; address
# operands are wildcarded (``None``).  Derived by byte analysis of HVSC
# representatives (not copied from any signature database), and used only to
# *label* a recovered model, never to gate recovery.  A play prologue does not
# map 1:1 to a sidid ``Vnn`` sub-version -- several sidid versions share one --
# so these are honest prologue-class tags, not exact version numbers (use
# ``sidid`` for the precise sub-version).  V20's prologue is distinctive.
_PROLOGUE_CLASSES: Tuple[Tuple[str, Tuple[Optional[int], ...]], ...] = (
    ("V20", (0xA5, 0xFB, 0x48, 0xA5, 0xFC, 0x48, 0xCE, None, None, 0x10, 0x1D, 0xAD)),
    (
        "V9-class",
        (0xA5, 0xFB, 0x48, 0xA5, 0xFC, 0x48, 0xA2, 0x02, 0xCE, None, None, 0x10),
    ),
    (
        "V13/V14-class",
        (0xA2, 0x02, 0xBD, None, None, 0xC9, 0x02, 0xD0, None, 0xBC, None, None),
    ),
    (
        "V11/V17-class",
        (0xA5, 0xFB, 0x48, 0xA5, 0xFC, 0x48, 0xA2, 0x02, 0xBD, 0x06, 0x10, 0xD0),
    ),
    (
        "V15/V18-class",
        (0xA2, 0x02, 0xA5, 0xFB, 0x48, 0xA5, 0xFC, 0x48, 0xBD, 0x06, 0x10),
    ),
)


def _match_masked(window: bytes, pattern: Tuple[Optional[int], ...]) -> bool:
    if len(window) < len(pattern):
        return False
    return all(p is None or window[i] == p for i, p in enumerate(pattern))


def classify_version(load: int, image: bytes, play: int) -> str:
    """Best-effort prologue-class tag for ``image`` (else ``JCH_NewPlayer``).

    Reads the play routine at offset ``play - load``.  See ``_PROLOGUE_CLASSES``:
    the tag is a play-prologue class, not an exact sidid sub-version.
    """
    play_off = play - load
    if not 0 <= play_off < len(image):
        return "JCH_NewPlayer"
    window = image[play_off : play_off + 16]
    for tag, pattern in _PROLOGUE_CLASSES:
        if _match_masked(window, pattern):
            return tag
    return "JCH_NewPlayer"
