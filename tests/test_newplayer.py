"""Unit tests for the JCH NewPlayer wavetable-family reader.

These build a synthetic-but-coherent family image in memory, so they exercise
the whole discovery + coherence + accessor surface offline (no HVSC needed).
"""

import pytest

from pyjch import newplayer
from pyjch.errors import SidParseError
from pyjch.newplayer import NewPlayerModel, classify_version, discover, recover

LOAD = 0x1000
INIT = 0x1000
PLAY = 0x1003

# Synthetic table addresses (all inside a 0x200-byte image at $1000).
SUB = 0x1100  # subtune table
PLO = 0x1120  # pattern-pointer low table
PHI = 0x1130  # pattern-pointer high table
INSTR = 0x1140  # instrument records
OL0, OL1, OL2 = 0x1160, 0x1170, 0x1180  # per-voice order lists
PAT0, PAT1 = 0x1190, 0x11A0  # pattern bases


def _put(buf, addr, data):
    off = addr - LOAD
    buf[off : off + len(data)] = data


def _base_image():
    """A minimal coherent JCH NewPlayer family image (load $1000, 0x200 bytes)."""
    buf = bytearray(0x200)
    # Entry vectors: JMP init ($1040) ; JMP play ($10C1).
    _put(buf, 0x1000, b"\x4c\x40\x10\x4c\xc1\x10")
    # Discovery idioms.
    _put(buf, 0x1006, bytes([0xA2, 0x00, 0xB9, SUB & 0xFF, SUB >> 8, 0x9D]))
    _put(buf, 0x1010, bytes([0xB9, PLO & 0xFF, PLO >> 8, 0x85, 0xFB]))
    _put(buf, 0x1018, bytes([0xB9, PHI & 0xFF, PHI >> 8, 0x85, 0xFC]))
    _put(
        buf,
        0x1020,
        bytes([0xB9, INSTR & 0xFF, INSTR >> 8, 0xBC, 0x00, 0x10, 0x99, 0x05, 0xD4]),
    )
    # Subtune 0 record: order pointers v0/v1/v2, tempo, pad.
    _put(
        buf,
        SUB,
        bytes(
            [
                OL0 & 0xFF,
                OL0 >> 8,
                OL1 & 0xFF,
                OL1 >> 8,
                OL2 & 0xFF,
                OL2 >> 8,
                0x06,
                0x00,
            ]
        ),
    )
    # Pattern-pointer tables (two patterns).
    _put(buf, PLO, bytes([PAT0 & 0xFF, PAT1 & 0xFF]))
    _put(buf, PHI, bytes([PAT0 >> 8, PAT1 >> 8]))
    # Instrument 0 record.
    _put(buf, INSTR, bytes([0x05, 0x58, 0x80, 0xF1, 0x04, 0x04, 0x00, 0x00]))
    # Order lists: a transpose prefix + pattern index, then loop.
    _put(buf, OL0, bytes([0x8C, 0x00, 0x01, 0xFF]))  # $8C transpose, pat 0, pat 1, loop
    _put(buf, OL1, bytes([0x01, 0xFE]))  # pat 1, stop
    _put(buf, OL2, bytes([0x00, 0xFF]))  # pat 0, loop
    return bytes(buf)


def _recover(image=None, num_subtunes=1):
    return recover(
        LOAD, INIT, PLAY, "n", "a", "r", image or _base_image(), b"", "T", num_subtunes
    )


def test_recover_coherent_model():
    model = _recover()
    assert isinstance(model, NewPlayerModel)
    assert (model.subtune_table, model.patternptr_lo, model.patternptr_hi) == (
        SUB,
        PLO,
        PHI,
    )
    assert model.instruments == INSTR
    assert model.orderlist_ptr(0, 0) == OL0
    assert model.orderlist_ptr(0, 2) == OL2
    assert model.subtune_tempo(0) == 0x06
    assert model.pattern_ptr(0) == PAT0
    assert model.pattern_ptr(1) == PAT1
    assert model.instrument(0) == [0x05, 0x58, 0x80, 0xF1, 0x04, 0x04, 0x00, 0x00]


def test_iter_orderlist_walks_and_terminates():
    model = _recover()
    # v0: transpose prefix skipped; pattern indices 0 then 1.
    assert model.iter_orderlist(0, 0) == [0, 1]
    assert model.iter_orderlist(0, 1) == [1]
    assert model.iter_orderlist(0, 2) == [0]


def test_discover_returns_bases():
    bases = discover(LOAD, _base_image(), INIT, PLAY)
    assert bases["subtune_table"] == SUB
    assert bases["patternptr_lo"] == PLO


def test_foreign_image_not_discovered():
    assert discover(LOAD, bytes(0x200), INIT, PLAY) is None
    with pytest.raises(SidParseError):
        _recover(image=bytes(0x200))


def test_bad_entry_vectors_rejected():
    buf = bytearray(_base_image())
    buf[0] = 0x00  # clobber the JMP init opcode
    with pytest.raises(SidParseError):
        _recover(image=bytes(buf))


def test_orderlist_pointer_out_of_range_rejected():
    buf = bytearray(_base_image())
    _put(buf, SUB, bytes([0x00, 0xFF]))  # v0 order ptr -> $FF00, out of image
    with pytest.raises(SidParseError):
        _recover(image=bytes(buf))


def test_orderlist_never_terminates_rejected():
    buf = bytearray(_base_image())
    # Fill v0's order list with in-range pattern-0 refs and no terminator.
    _put(buf, OL0, bytes([0x00]) * 0x40)
    with pytest.raises(SidParseError):
        _recover(image=bytes(buf))


def test_pattern_index_out_of_range_rejected():
    buf = bytearray(_base_image())
    _put(buf, PHI, bytes([0xFF, 0xFF]))  # pattern bases -> out of image
    with pytest.raises(SidParseError):
        _recover(image=bytes(buf))


def test_second_subtune_out_of_range_rejected():
    # Claim two subtunes but the table only holds one -> subtune 1 out of range.
    with pytest.raises(SidParseError):
        _recover(num_subtunes=2)


def test_recognize_returns_anchor_then_none():
    assert newplayer.recognize(LOAD, _base_image(), INIT, PLAY) == SUB
    assert newplayer.recognize(LOAD, bytes(0x200), INIT, PLAY) is None


def test_classify_version_v20_prologue():
    buf = bytearray(_base_image())
    # Plant the distinctive V20 play prologue at the play routine ($10C1).
    _put(
        buf,
        0x10C1,
        bytes([0xA5, 0xFB, 0x48, 0xA5, 0xFC, 0x48, 0xCE, 0x46, 0x17, 0x10, 0x1D, 0xAD]),
    )
    # play JMP target must point at the prologue for classify to read it.
    _put(buf, 0x1003, b"\x4c\xc1\x10")
    assert classify_version(LOAD, bytes(buf), 0x10C1) == "V20"
    assert classify_version(LOAD, _base_image(), PLAY) == "JCH_NewPlayer"


def test_classify_version_play_out_of_range():
    assert classify_version(LOAD, _base_image(), 0x9999) == "JCH_NewPlayer"


PITCH = 0x11C0  # ascending 16-bit pitch table for the V17-idiom tests


def _v17_pitch_image():
    """Base image + a V17 interleaved pitch read and an ascending pitch table."""
    buf = bytearray(_base_image())
    # TAY ; LDA PITCH,Y ; STA freqlo,X ; LDA PITCH+1,Y (no ADC #$00 anywhere).
    _put(
        buf,
        0x1030,
        bytes(
            [0xA8, 0xB9, PITCH & 0xFF, PITCH >> 8, 0x9D, 0x0C, 0x10, 0xB9]
            + [(PITCH + 1) & 0xFF, PITCH >> 8, 0x9D, 0x0F, 0x10]
        ),
    )
    for i in range(96):  # ascending 16-bit LE table
        _put(buf, PITCH + 2 * i, bytes([(0x0100 + i) & 0xFF, (0x0100 + i) >> 8]))
    return bytes(buf)


def test_v17_interleaved_pitch_idiom_recovered():
    """The V17 idiom recovers the pitch base when the ADC #$00 idiom is absent."""
    bases = discover(LOAD, _v17_pitch_image(), INIT, PLAY)
    assert bases["pitch_table"] == PITCH


def test_v17_pitch_idiom_rejects_non_ascending_table():
    """A matching read frame over a non-monotonic table is not accepted as pitch."""
    buf = bytearray(_v17_pitch_image())
    _put(buf, PITCH, bytes([0xFF, 0xFF, 0x00, 0x00]))  # breaks monotonicity at entry 0
    bases = discover(LOAD, bytes(buf), INIT, PLAY)
    assert "pitch_table" not in bases


def test_pitch_lookup_helpers_negative():
    """The lookup finder returns None with no frame; coherence rejects a short span."""
    assert newplayer._find_pitch_lookup(bytes(0x40)) is None
    assert not newplayer._pitch_coherent(bytes(0x20), LOAD, LOAD + 0x10)


STREAM = 0x11E0  # V1/V2 interleaved wave stream base
CURSOR = 0x16FA  # per-voice wave cursor cell (a runtime cell, off-image)
RESTART = 0x16FB  # per-voice restart-target cell (off-image)


def _interleaved_wave_image():
    """Base image + the V1/V2 interleaved-wave stream-read and restart idioms."""
    buf = bytearray(_base_image())
    # Stream read: LDY cursor,X ; LDA STREAM,Y ; CMP #$FF.
    _put(
        buf,
        0x1050,
        bytes([0xBC, CURSOR & 0xFF, CURSOR >> 8, 0xB9, STREAM & 0xFF, STREAM >> 8])
        + b"\xc9\xff",
    )
    # Restart handler: CMP #$FF ; BNE +9 ; LDA restart,X ; STA cursor,X ; JMP.
    _put(
        buf,
        0x1060,
        bytes([0xC9, 0xFF, 0xD0, 0x09, 0xBD, RESTART & 0xFF, RESTART >> 8, 0x9D])
        + bytes([CURSOR & 0xFF, CURSOR >> 8, 0x4C, 0x00, 0x10]),
    )
    return bytes(buf)


def test_interleaved_wave_stream_discovered():
    """The V1/V2 idiom recovers the interleaved wave-stream base (no two-column)."""
    bases = discover(LOAD, _interleaved_wave_image(), INIT, PLAY)
    assert bases["wave_stream"] == STREAM
    assert "wave_note_col" not in bases  # single stream, not two parallel columns


def test_interleaved_wave_absent_in_base_image():
    """A plain family image has no interleaved-wave stream."""
    assert newplayer._find_interleaved_wave(_base_image()) is None
    assert "wave_stream" not in discover(LOAD, _base_image(), INIT, PLAY)


def test_interleaved_wave_mismatched_cursor_rejected():
    """The restart handler must reference the same cursor cell as the stream read."""
    buf = bytearray(_interleaved_wave_image())
    _put(buf, 0x1068, bytes([0x00, 0x17]))  # STA cursor,X -> a different cell
    assert newplayer._find_interleaved_wave(bytes(buf)) is None
