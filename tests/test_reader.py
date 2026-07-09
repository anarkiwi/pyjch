"""Reader: container parsing and per-tune discovery."""

import struct

import pytest

from pyjch import constants, reader
from pyjch.errors import SidParseError


def test_discovers_immediates_and_bases(tune_path):
    """The reader discovers the per-tune immediates and table bases."""
    song = reader.read(tune_path)
    assert song.load_addr == 0x1000
    # Discovered immediates are plain bytes.
    for imm in (song.init_ad, song.init_sr, song.gate_ctrl, song.gateoff_ctrl):
        assert 0 <= imm <= 0xFF
    # Relocated subpattern pointer-table bases are in the loaded image range.
    assert song.load_addr <= song.subptr_lo
    assert song.load_addr <= song.subptr_hi
    assert len(song.freq_lo) == constants.FREQ_TABLE_LEN
    assert len(song.freq_hi) == constants.FREQ_TABLE_LEN


def test_orderlist_and_subpattern_pointers(tune_path):
    """Orderlist and subpattern pointers resolve into the loaded image."""
    song = reader.read(tune_path)
    for voice in range(constants.VOICES):
        addr = song.orderlist_ptr(0, voice)
        assert song.load_addr <= addr < song.load_addr + len(song.image)
    # Subpattern 0 resolves to some address (data may be sparse).
    assert song.subpattern_ptr(0) >= 0


def test_read_from_bytes_matches_path(tune_path):
    """parse(bytes) and read(path) agree."""
    data = tune_path.read_bytes()
    a = reader.parse(data)
    b = reader.read(tune_path)
    assert a.image == b.image
    assert a.subptr_lo == b.subptr_lo


def test_read_file_like(tune_path):
    """read() accepts a file-like object."""
    with open(tune_path, "rb") as handle:
        song = reader.read(handle)
    assert song.load_addr == 0x1000


def test_read_rejects_unknown_source():
    with pytest.raises(TypeError):
        reader.read(12345)


def test_parse_rejects_truncated_psid():
    with pytest.raises(SidParseError):
        reader.parse(b"PSID\x00\x01")


def test_parse_rejects_truncated_prg():
    with pytest.raises(SidParseError):
        reader.parse(b"\x00")


def _canonical_image(size=0x400):
    """A minimal image carrying every V0x discovery idiom the reader needs."""
    image = bytearray(size)
    idioms = (
        b"\xa9\x0f\x8d\x05\xd4"  # LDA #$0F ; STA $D405  (attack/decay)
        b"\xa9\x06\x8d\x06\xd4"  # LDA #$06 ; STA $D406  (sustain/release)
        b"\xa9\x10\x99\x04\xd4"  # LDA #$10 ; STA $D404,Y (gate-off CTRL)
        b"\xa9\x21\x99\x04\xd4"  # LDA #$21 ; STA $D404,Y (gate-on CTRL)
        b"\xb9\x7e\x13\x85\xfb"  # LDA $137E,Y ; STA $FB (subptr-lo base)
        b"\xb9\x83\x13\x85\xfc"  # LDA $1383,Y ; STA $FC (subptr-hi base)
    )
    image[0x40 : 0x40 + len(idioms)] = idioms
    return bytes(image)


def test_parse_prg_load_address():
    """A bare .prg uses its 2-byte little-endian load address."""
    image = _canonical_image()
    prg = struct.pack("<H", 0x1000) + image
    song = reader.parse(prg)
    assert song.load_addr == 0x1000
    assert song.image == image
    assert song.init_ad == 0x0F
    assert song.init_sr == 0x06
    assert song.gateoff_ctrl == 0x10
    assert song.gate_ctrl == 0x21
    assert song.subptr_lo == 0x137E
    assert song.subptr_hi == 0x1383


def test_parse_rejects_foreign_player():
    """An image lacking the V0x idioms is rejected, not silently mis-parsed."""
    prg = struct.pack("<H", 0x1000) + bytes(0x400)
    with pytest.raises(SidParseError):
        reader.parse(prg)
