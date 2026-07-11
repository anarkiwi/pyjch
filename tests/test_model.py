"""Model: Song pointer resolution."""

from pyjch import constants
from pyjch.model import Song


def test_orderlist_ptr_indexing():
    """orderlist_ptr indexes the table by subtune*8 + voice*2."""
    load = 0x1000
    image = bytearray(0x100)
    # Three voice pointer pairs for subtune 0 at offset 0x10.
    pairs = [(0x40, 0x11), (0x60, 0x11), (0x80, 0x11)]
    for voice, (lo, hi) in enumerate(pairs):
        image[constants.ORDERLIST_PTR_TABLE + voice * 2] = lo
        image[constants.ORDERLIST_PTR_TABLE + voice * 2 + 1] = hi
    song = Song(load_addr=load, image=bytes(image), orderlist_ptr_table=load + 0x10)
    for voice, (lo, hi) in enumerate(pairs):
        assert song.orderlist_ptr(0, voice) == (lo | (hi << 8))


def test_subpattern_ptr_indexing():
    load = 0x1000
    image = bytearray(0x400)
    sub_lo = load + 0x200
    sub_hi = load + 0x280
    image[0x200 + 5] = 0x34
    image[0x280 + 5] = 0x12
    song = Song(load_addr=load, image=bytes(image), subptr_lo=sub_lo, subptr_hi=sub_hi)
    assert song.subpattern_ptr(5) == 0x1234


def test_byte_out_of_range_defaults_zero():
    song = Song(load_addr=0x1000, image=b"\x01\x02")
    assert song.subpattern_ptr(99) == 0
