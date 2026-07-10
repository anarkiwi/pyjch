"""Offline tests for the editor-native ``.prg`` writer (NP20-25)."""

import dataclasses

import pytest

from pyjch import editor, reader
from pyjch.errors import SidParseError
from pyjch.extract import extract

from tests import _synth_v20 as synth


def _tune():
    return extract(reader.parse(synth.build_psid()))


def _image(prg):
    """Strip the 2-byte load address; return (load, word, byte) accessors."""
    load = prg[0] | (prg[1] << 8)
    img = prg[2:]

    def byte(addr):
        return img[addr - load]

    def word(addr):
        return byte(addr) | (byte(addr + 1) << 8)

    return load, word, byte


def _read_ptr(profile, word):
    """Reader for the ``$0FA0`` pointer array: index -> resolved base."""
    return {i: word(profile.ptr_addr(i)) for i in range(0x02, 0x19)}


def _decode_order(byte, base):
    """Decode an editor order list back to ``(pattern, transpose)`` entries."""
    out = []
    addr = base
    while byte(addr) != 0xFF:
        out.append((byte(addr + 1), byte(addr)))
        addr += 2
    return out


def test_roundtrip_np25_layout_and_tables():
    tune = _tune()
    profile = editor.NP25_PROFILE
    prg = editor.write_editor_prg(tune, driver=b"", profile=profile)
    assert prg[0] == profile.load_addr & 0xFF and prg[1] == profile.load_addr >> 8

    load, word, byte = _image(prg)
    assert load == profile.load_addr

    # version magic (5 chars) and default tempo at init-data + 6.
    assert bytes(byte(profile.ptr_magic + i) for i in range(5)) == b"25.FX"
    assert byte(profile.base_init_data + 6) == tune.subtunes[0].tempo

    # every $0FA0[i] resolves to the expected canonical base.
    ptr = _read_ptr(profile, word)
    assert ptr[profile.idx_pitch] == profile.base_pitch
    assert ptr[profile.idx_finetune] == profile.base_finetune
    assert ptr[profile.idx_wave] == profile.base_wave_note
    assert ptr[profile.idx_wave2] == profile.base_wave_ctrl
    assert profile.base_wave_ctrl == profile.base_wave_note + 0x100
    assert ptr[profile.idx_filter] == profile.base_filter
    assert ptr[profile.idx_pulse] == profile.base_pulse
    assert ptr[profile.idx_inst] == profile.base_inst
    assert [ptr[i] for i in profile.idx_order] == list(profile.base_order)
    assert ptr[profile.idx_seqlo] == profile.base_seqlo
    assert ptr[profile.idx_seqhi] == profile.base_seqhi
    assert ptr[profile.idx_cmd] == profile.base_cmd

    # tables sit byte-faithfully at their bases.
    assert [
        byte(profile.base_wave_note + i) for i in range(len(tune.wavetable.note_col))
    ] == tune.wavetable.note_col
    assert [byte(profile.base_inst + i) for i in range(8)] == tune.instruments[0].raw
    assert [byte(profile.base_cmd + i) for i in range(2)] == [
        tune.commands[0].lo,
        tune.commands[0].hi,
    ]
    pitch = [byte(profile.base_pitch + i) for i in range(len(tune.pitch_table))]
    assert pitch == tune.pitch_table
    filt = tune.filter_program[0]
    assert [byte(profile.base_filter + i) for i in range(4)] == [
        filt.value,
        filt.step,
        filt.dwell,
        filt.next_off,
    ]

    # order lists decode back to the recovered (pattern, transpose) entries.
    for voice, order in enumerate(tune.subtunes[0].order_lists):
        got = _decode_order(byte, profile.base_order[voice])
        assert got == [(e.pattern, e.transpose) for e in order.entries]

    # seq lo/hi tables resolve to the emitted, $7F-terminated pair streams.
    for index, events in enumerate(tune.patterns):
        seq = byte(profile.base_seqlo + index) | (byte(profile.base_seqhi + index) << 8)
        expected, _notes = editor._encode_sequence(index, events)
        assert [byte(seq + i) for i in range(len(expected))] == expected
        assert expected[-1] == 0x7F


@pytest.mark.parametrize(
    "profile,magic",
    [
        (editor.NP20_PROFILE, b"20.G4"),
        (editor.NP21_PROFILE, b"21.G5"),
        (editor.NP22_PROFILE, b"22.4X"),
        (editor.NP23_PROFILE, b"23.FX"),
        (editor.NP24_PROFILE, b"24.1X"),
        (editor.NP25_PROFILE, b"25.FX"),
    ],
)
def test_each_profile_emits_its_version_magic(profile, magic):
    prg = editor.write_editor_prg(_tune(), profile=profile)
    _load, _word, byte = _image(prg)
    assert bytes(byte(profile.ptr_magic + i) for i in range(5)) == magic


def test_np2x_profile_lookup():
    assert editor.NP2X_PROFILE(23) is editor.NP23_PROFILE
    assert editor.np_profile(20) is editor.NP20_PROFILE
    with pytest.raises(SidParseError):
        editor.NP2X_PROFILE(26)
    with pytest.raises(SidParseError):
        editor.np_profile(19)


def test_capacity_overflow_raises():
    tune = _tune()
    over = dataclasses.replace(tune, instruments=tune.instruments * 9)
    with pytest.raises(SidParseError):
        editor.write_editor_prg(over)
    rows = dataclasses.replace(tune, patterns=[tune.patterns[0] * 50])
    with pytest.raises(SidParseError):
        editor.write_editor_prg(rows)


def test_missing_table_raises_clear_message():
    tune = _tune()
    no_wave = dataclasses.replace(tune, wavetable=None)
    with pytest.raises(SidParseError, match="wave table"):
        editor.write_editor_prg(no_wave)
    no_pitch = dataclasses.replace(tune, pitch_table=[])
    with pytest.raises(SidParseError, match="pitch table"):
        editor.write_editor_prg(no_pitch)


def test_sequence_reencoding_records_dropped_control_bytes():
    tune = _tune()
    # pattern 4 carries a packed $83 duration byte with no editor-pair form.
    _data, notes = editor._encode_sequence(4, tune.patterns[4])
    assert any("83" in note for note in notes)
