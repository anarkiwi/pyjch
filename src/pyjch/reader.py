"""Read a JCH NewPlayer tune (PSID/RSID/.sid or .prg image) into a Song.

A PSID/RSID container wraps the raw C64 image (player code + song data)
with a header giving the load/init/play addresses.  The JCH NewPlayer
binary is identical across tunes of the *same player version*; only its
DATA differs, and each per-tune immediate / pointer-table base lives in
the player code as an instruction operand.  The reader locates those
operands by their surrounding instruction bytes -- an idiom search rather
than a fixed offset -- so it is robust both to relocation *and* to the
small per-tune code shifts that a fixed offset cannot survive.

Supported version.  HVSC labels (via ``sidid``) at least twenty distinct
JCH ``NewPlayer`` binaries (V1..V21, plus ``Glover_NewPlayer_V21`` and
``JCH_DigiPlayer``); they are genuinely different players, not just
relocations.  This reader/player pair models the canonical
``JCH_NewPlayer_V0x`` layout: attack/decay and sustain/release seeded by
immediates (``LDA #imm ; STA $D405/$D406``), gate-on and gate-off CTRL
seeded by immediates (``LDA #imm ; STA $D404,Y``), and split subpattern
pointer tables loaded via ``LDA abs,Y ; STA $fb/$fc``.  :func:`parse`
discovers those idioms; when they are absent it raises -- it does not
silently return garbage from a foreign player.  See ``docs/versions.md``.
"""

from pathlib import Path
from typing import Any, Optional, Tuple

from pysidtracker import BaseSidParser, SidImage
from pysidtracker import SidParseError as _BaseSidParseError

from pyjch import constants
from pyjch.errors import SidParseError
from pyjch.model import Song


def _parse_container(
    data: bytes,
) -> Tuple[int, int, int, str, str, str, bytes, bytes]:
    """Return (load, init, play, name, author, released, image, header)."""
    try:
        img = SidImage.from_bytes(data)
    except _BaseSidParseError as exc:
        raise SidParseError(str(exc)) from exc
    header = img.header
    if header is None:
        # Bare .prg: 2-byte little-endian load address + image.
        return (
            img.load,
            constants.DEFAULT_INIT,
            constants.DEFAULT_PLAY,
            "",
            "",
            "",
            img.image,
            img.container,
        )
    load = img.load
    # Header init/play of 0 mean "same as load" for JCH NewPlayer tunes.
    init = header.init_address or load
    play = header.play_address or load
    return (
        load,
        init,
        play,
        header.name,
        header.author,
        header.released,
        img.image,
        img.container,
    )


def _byte(image: bytes, off: int) -> int:
    if 0 <= off < len(image):
        return image[off]
    raise SidParseError(f"operand offset {off:#x} past end of image")


def _find_operands(image: bytes, prefix: bytes, suffix: bytes, oplen: int) -> list:
    """Every operand framed by ``prefix .. suffix`` (``oplen`` bytes wide, LE).

    Locates the instruction idiom ``prefix<operand>suffix`` and returns each
    operand as a little-endian integer, in image order.
    """
    out = []
    start = 0
    step = len(prefix) + oplen + len(suffix)
    while True:
        hit = image.find(prefix, start)
        if hit < 0:
            break
        start = hit + 1
        op_at = hit + len(prefix)
        if image[op_at + oplen : op_at + oplen + len(suffix)] != suffix:
            continue
        if op_at + oplen > len(image):
            continue
        value = 0
        for i in range(oplen):
            value |= image[op_at + i] << (8 * i)
        out.append(value)
        start = hit + step
    return out


def _one_operand(
    image: bytes, prefix: bytes, suffix: bytes, oplen: int, what: str
) -> int:
    """The first ``prefix<operand>suffix`` operand, or raise if the idiom is absent."""
    found = _find_operands(image, prefix, suffix, oplen)
    if not found:
        raise SidParseError(
            f"not a canonical JCH NewPlayer (V0x): {what} idiom not found"
        )
    return found[0]


# Player-code idioms carrying the per-tune values (see module docstring).
_AD_SUF = b"\x8d\x05\xd4"  # STA $D405 (attack/decay -> voice 0)
_SR_SUF = b"\x8d\x06\xd4"  # STA $D406 (sustain/release -> voice 0)
_GATE_SUF = b"\x99\x04\xd4"  # STA $D404,Y (CTRL; gate-off then gate-on)
_LDA_IMM = b"\xa9"  # LDA #imm
_LDA_ABSY = b"\xb9"  # LDA abs,Y


def parse(data: bytes) -> Song:
    """Parse JCH NewPlayer tune bytes (PSID/RSID/.sid or .prg) into a Song.

    Raises :class:`~pyjch.errors.SidParseError` when the image is not the
    canonical ``JCH_NewPlayer_V0x`` layout this player models (the discovery
    idioms are absent) -- rather than returning meaningless values read out of
    a different player's code.
    """
    load, init, play, name, author, released, image, header = _parse_container(data)

    # Per-tune DISCOVERY via instruction idioms (relocation- and shift-safe).
    init_ad = _one_operand(image, _LDA_IMM, _AD_SUF, 1, "attack/decay init")
    init_sr = _one_operand(image, _LDA_IMM, _SR_SUF, 1, "sustain/release init")
    gates = _find_operands(image, _LDA_IMM, _GATE_SUF, 1)
    if len(gates) < 2:
        raise SidParseError(
            "not a canonical JCH NewPlayer (V0x): gate-on/off CTRL immediates "
            f"not found (need 2, found {len(gates)})"
        )
    gateoff_ctrl, gate_ctrl = gates[0], gates[1]
    subptr_lo = _one_operand(image, _LDA_ABSY, b"\x85\xfb", 2, "subpattern ptr-lo base")
    subptr_hi = _one_operand(image, _LDA_ABSY, b"\x85\xfc", 2, "subpattern ptr-hi base")

    freq_lo = [
        _byte(image, constants.FREQ_LO + n) for n in range(constants.FREQ_TABLE_LEN)
    ]
    freq_hi = [
        _byte(image, constants.FREQ_HI + n) for n in range(constants.FREQ_TABLE_LEN)
    ]

    return Song(
        load_addr=load,
        init_addr=init,
        play_addr=play,
        name=name,
        author=author,
        released=released,
        init_ad=init_ad,
        init_sr=init_sr,
        gate_ctrl=gate_ctrl,
        gateoff_ctrl=gateoff_ctrl,
        subptr_lo=subptr_lo,
        subptr_hi=subptr_hi,
        orderlist_ptr_table=load + constants.ORDERLIST_PTR_TABLE,
        freq_lo=freq_lo,
        freq_hi=freq_hi,
        image=image,
        container_header=header,
    )


def read(src) -> Song:
    """Read a JCH NewPlayer tune from a path, bytes, or file-like object."""
    if isinstance(src, bytes):
        return parse(src)
    if isinstance(src, (str, Path)):
        return parse(Path(src).read_bytes())
    if hasattr(src, "read"):
        return parse(src.read())
    raise TypeError(f"cannot read a song from {type(src).__name__}")


# Canonical JCH_NewPlayer_V0x init signature: STA $D405 ; STA $D40C (seed the
# attack/decay default into voices 0 and 1).  The SID register addresses are
# load-independent, so this anchor survives relocation; empirically it is
# present in every V0x tune and absent from the other NewPlayer versions.
_RECOGNIZE_SIG = b"\x8d\x05\xd4\x8d\x0c\xd4"


class JchSidParser(BaseSidParser):
    """:class:`~pysidtracker.BaseSidParser` adapter for the JCH NewPlayer.

    Gives the JCH reader the shared ``read``/``parse``/``detect`` surface.
    :meth:`recognize` anchors on the canonical ``JCH_NewPlayer_V0x`` init
    signature (a load-independent SID-register store idiom), so :meth:`detect`
    classifies a supported tune as ``DIRECT`` and reports ``UNKNOWN`` for the
    foreign NewPlayer versions this player does not model.
    """

    error_class: type = SidParseError

    def parse(self, data: bytes, **kwargs: Any) -> Song:
        """Decode raw ``.sid``/``.prg`` ``data`` into a :class:`~pyjch.model.Song`."""
        return parse(data, **kwargs)

    def recognize(self, image: SidImage) -> Optional[object]:
        """Return the canonical V0x signature address in ``image``, else ``None``."""
        addr = image.find(_RECOGNIZE_SIG)
        return addr if addr >= 0 else None
