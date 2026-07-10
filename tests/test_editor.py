"""Offline tests for the editor-native ``.prg`` write-side scaffold."""

import dataclasses

import pytest

from pyjch import editor, reader
from pyjch.errors import SidParseError
from pyjch.extract import extract

from tests import _synth_v20 as synth


def _tune():
    return extract(reader.parse(synth.build_psid()))


def _image(prg):
    """Strip the 2-byte load address; return (bytes, load, word, byte helpers)."""
    load = prg[0] | (prg[1] << 8)
    img = prg[2:]

    def byte(addr):
        return img[addr - load]

    def word(addr):
        return byte(addr) | (byte(addr + 1) << 8)

    return img, load, word, byte


def test_pointer_block_and_tables_at_canonical_addresses():
    tune = _tune()
    profile = editor.NP20_PROFILE
    prg = editor.write_editor_prg(tune, driver=bytes(0x2000))
    assert prg[0] == profile.load_addr & 0xFF and prg[1] == profile.load_addr >> 8

    _img, _load, word, byte = _image(prg)
    # pointer block resolves to the canonical bases
    assert word(profile.ptr_wave) == profile.base_wave_note
    assert word(profile.ptr_filter) == profile.base_filter
    assert word(profile.ptr_pulse) == profile.base_pulse
    assert word(profile.ptr_inst) == profile.base_inst
    assert word(profile.ptr_cmd) == profile.base_cmd
    assert [word(a) for a in profile.ptr_order] == list(profile.base_order)
    assert word(profile.ptr_seqlo) == profile.base_seqlo
    assert word(profile.ptr_seqhi) == profile.base_seqhi
    # version magic + default tempo
    assert bytes(byte(profile.ptr_magic + i) for i in range(4)) == b"20.G"
    assert byte(profile.base_init_data + 6) == tune.subtunes[0].tempo

    # table bytes sit at their bases
    assert [
        byte(profile.base_wave_note + i) for i in range(4)
    ] == tune.wavetable.note_col[:4]
    assert [byte(profile.base_inst + i) for i in range(8)] == tune.instruments[0].raw
    assert [byte(profile.base_cmd + i) for i in range(2)] == [
        tune.commands[0].lo,
        tune.commands[0].hi,
    ]
    # order list (recovered packed encoding: transpose prefix + $FF loop)
    assert byte(profile.base_order[0] + 3) == 0xFF
    assert byte(profile.base_order[1]) == 0x85 and byte(profile.base_order[1] + 1) == 2

    # sequence pointer table resolves to the sequence data ($7F terminated)
    seq = byte(profile.base_seqlo) | (byte(profile.base_seqhi) << 8)
    assert seq == profile.base_seq
    events = tune.patterns[0]
    assert [byte(seq + i) for i in range(len(events))] == [e.raw for e in events]
    assert byte(seq + len(events)) == 0x7F


def test_capacity_overflow_raises():
    tune = _tune()
    over = dataclasses.replace(tune, instruments=tune.instruments * 9)
    with pytest.raises(SidParseError):
        editor.write_editor_prg(over, driver=b"")
    rows = dataclasses.replace(tune, patterns=[tune.patterns[0] * 50])
    with pytest.raises(SidParseError):
        editor.write_editor_prg(rows, driver=b"")


def test_unresolved_profile_parts_raise_not_implemented():
    tune = _tune()
    no_inst = editor.Np20Profile(instrument_record_width=None)
    with pytest.raises(NotImplementedError):
        editor.write_editor_prg(tune, driver=b"", profile=no_inst)
    no_pitch = editor.Np20Profile(pitch_resolved=False)
    with pytest.raises(NotImplementedError):
        editor.write_editor_prg(tune, driver=b"", profile=no_pitch)
