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

    # every table sits byte-for-byte at its base (verbatim from the recovery).
    def region(base, size):
        return [byte(base + i) for i in range(size)]

    assert region(profile.base_wave_note, len(tune.wavetable.note_col)) == (
        tune.wavetable.note_col
    )
    assert region(profile.base_wave_ctrl, len(tune.wavetable.ctrl_col)) == (
        tune.wavetable.ctrl_col
    )
    for index, inst in enumerate(tune.instruments):
        assert region(profile.base_inst + index * 8, 8) == inst.raw
    cmd_bytes = [b for c in tune.commands for b in (c.lo, c.hi)]
    assert region(profile.base_cmd, len(cmd_bytes)) == cmd_bytes
    pulse = [
        b for s in tune.pw_program for b in (s.reset, s.step, s.dir_rate, s.next_off)
    ]
    assert region(profile.base_pulse, len(pulse)) == pulse
    filt = [
        b for s in tune.filter_program for b in (s.value, s.step, s.dwell, s.next_off)
    ]
    assert region(profile.base_filter, len(filt)) == filt
    assert region(profile.base_pitch, len(tune.pitch_table)) == tune.pitch_table

    # order lists are emitted verbatim (source bytes incl. $FF/$FE terminator).
    for voice, order in enumerate(tune.subtunes[0].order_lists):
        assert region(profile.base_order[voice], len(order.raw)) == order.raw

    # seq lo/hi tables resolve to the verbatim, $7F-terminated source bytes.
    for index, raw in enumerate(tune.pattern_raw):
        seq = byte(profile.base_seqlo + index) | (byte(profile.base_seqhi + index) << 8)
        assert [byte(seq + i) for i in range(len(raw))] == raw
        assert raw[-1] == 0x7F


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
