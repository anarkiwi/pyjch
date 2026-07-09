"""Read a JCH NewPlayer tune (PSID/RSID/.sid or .prg image) into a Song.

A PSID/RSID container wraps the raw C64 image (player code + song data)
with a header giving the load/init/play addresses.  The JCH NewPlayer
binary is identical across tunes; only its DATA differs, and each per-tune
immediate / pointer-table base lives at a fixed offset in the player code
as an instruction operand.  The reader reads those operands to discover
the per-tune values (relocation-safe -- no fixed-address assumption), then
exposes the orderlist pointers, frequency tables and subpattern tables.
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


def _op16(image: bytes, off: int) -> int:
    if off + 1 < len(image):
        return image[off] | (image[off + 1] << 8)
    raise SidParseError(f"operand offset {off:#x} past end of image")


def parse(data: bytes) -> Song:
    """Parse JCH NewPlayer tune bytes (PSID/RSID/.sid or .prg) into a Song."""
    load, init, play, name, author, released, image, header = _parse_container(data)

    # Per-tune DISCOVERY (operand mode): immediates + relocated table bases.
    init_ad = _byte(image, constants.OP_INIT_AD)
    init_sr = _byte(image, constants.OP_INIT_SR)
    gateoff_ctrl = _byte(image, constants.OP_GATEOFF_CTRL)
    gate_ctrl = _byte(image, constants.OP_GATE_CTRL)
    subptr_lo = _op16(image, constants.OP_SUBPTR_LO)
    subptr_hi = _op16(image, constants.OP_SUBPTR_HI)

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


class JchSidParser(BaseSidParser):
    """:class:`~pysidtracker.BaseSidParser` adapter for the JCH NewPlayer.

    Gives the JCH reader the shared ``read``/``parse``/``detect`` surface. JCH
    has no fixed absolute magic -- its note-frequency table lives at a
    load-relative offset -- so no reliable static :meth:`recognize` anchor
    exists and :meth:`detect` falls back to the default.
    """

    error_class: type = SidParseError

    def parse(self, data: bytes, **kwargs: Any) -> Song:
        """Decode raw ``.sid``/``.prg`` ``data`` into a :class:`~pyjch.model.Song`."""
        return parse(data, **kwargs)

    def recognize(  # pylint: disable=unused-argument
        self, image: SidImage
    ) -> Optional[object]:
        # No fixed absolute anchor (the JCH frequency table is load-relative);
        # a robust static recogniser is not available, so defer to the default.
        return None
