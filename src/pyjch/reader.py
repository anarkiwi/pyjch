"""Read a JCH NewPlayer tune (PSID/RSID/.sid or .prg image) into a Song.

A PSID/RSID container wraps the raw C64 image (player code + song data)
with a header giving the load/init/play addresses.  The JCH NewPlayer
binary is identical across tunes; only its DATA differs, and each per-tune
immediate / pointer-table base lives at a fixed offset in the player code
as an instruction operand.  The reader reads those operands to discover
the per-tune values (relocation-safe -- no fixed-address assumption), then
exposes the orderlist pointers, frequency tables and subpattern tables.
"""

import struct
from pathlib import Path
from typing import Tuple

from pyjch import constants
from pyjch.errors import SidParseError
from pyjch.model import Song

_PSID_HEADER = struct.Struct(">4sHHHHHHHI")  # magic..speed


def _read_cstr(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("latin-1")


def _parse_container(
    data: bytes,
) -> Tuple[int, int, int, str, str, str, bytes, bytes]:
    """Return (load, init, play, name, author, released, image, header)."""
    magic = data[:4]
    if magic in (b"PSID", b"RSID"):
        if len(data) < _PSID_HEADER.size:
            raise SidParseError("truncated PSID/RSID header")
        _m, _ver, data_off, load, init, play, _songs, _start, _speed = (
            _PSID_HEADER.unpack_from(data, 0)
        )
        name = _read_cstr(data[22:54])
        author = _read_cstr(data[54:86])
        released = _read_cstr(data[86:118])
        body = data[data_off:]
        if load == 0:  # load address is the first 2 bytes of the body
            if len(body) < 2:
                raise SidParseError("truncated PSID body")
            load = body[0] | (body[1] << 8)
            header = data[: data_off + 2]
            image = body[2:]
        else:
            header = data[:data_off]
            image = body
        if init == 0:
            init = load
        if play == 0:
            play = load
        return load, init, play, name, author, released, image, header
    # Bare .prg: 2-byte little-endian load address + image.
    if len(data) < 2:
        raise SidParseError("truncated .prg image")
    load = data[0] | (data[1] << 8)
    return (
        load,
        constants.DEFAULT_INIT,
        constants.DEFAULT_PLAY,
        "",
        "",
        "",
        data[2:],
        data[:2],
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
