"""Offline tests for the editor-native ``.prg`` writer (NP20-25)."""

import dataclasses

import pytest

from pyjch import editor, reader
from pyjch.errors import SidParseError
from pyjch.extract import extract

from tests import _synth_v20 as synth
from tests._driver import NP20_BASES, NP25_BASES, make_driver

DRIVER = make_driver()


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


def test_roundtrip_np20_layout_and_tables():
    tune = _tune()
    profile = editor.NP20_PROFILE
    prg = editor.write_editor_prg(tune, driver=DRIVER, profile=profile)
    assert prg[0] == profile.load_addr & 0xFF and prg[1] == profile.load_addr >> 8

    load, word, byte = _image(prg)
    assert load == profile.load_addr

    # version magic (5 chars) and default tempo at init-data + 6.
    assert bytes(byte(profile.ptr_magic + i) for i in range(5)) == b"20.G4"
    assert byte(NP25_BASES["init"] + 6) == tune.subtunes[0].tempo
    assert [word(NP25_BASES["init"] + 2 * v) for v in range(3)] == [
        NP25_BASES[f"order{v}"] for v in range(3)
    ]

    # every $0FA0[i] resolves to the expected canonical base.
    ptr = _read_ptr(profile, word)
    assert ptr[profile.idx_pitch] == NP25_BASES["pitch"]
    assert ptr[profile.idx_finetune] == NP25_BASES["finetune"]
    assert ptr[profile.idx_wave] == NP25_BASES["wave"]
    assert ptr[profile.idx_wave2] == NP25_BASES["wave2"]
    assert ptr[profile.idx_filter] == NP25_BASES["filter"]
    assert ptr[profile.idx_pulse] == NP25_BASES["pulse"]
    assert ptr[profile.idx_inst] == NP25_BASES["inst"]
    orders = [NP25_BASES[f"order{v}"] for v in range(3)]
    assert [ptr[i] for i in profile.idx_order] == orders
    assert ptr[profile.idx_seqlo] == NP25_BASES["seqlo"]
    assert ptr[profile.idx_seqhi] == NP25_BASES["seqhi"]
    assert ptr[profile.idx_cmd] == NP25_BASES["cmd"]

    # every table sits byte-for-byte at its base (verbatim from the recovery).
    def region(base, size):
        return [byte(base + i) for i in range(size)]

    assert region(NP25_BASES["wave"], len(tune.wavetable.note_col)) == (
        tune.wavetable.note_col
    )
    assert region(NP25_BASES["wave2"], len(tune.wavetable.ctrl_col)) == (
        tune.wavetable.ctrl_col
    )
    for index, inst in enumerate(tune.instruments):
        assert region(NP25_BASES["inst"] + index * 8, 8) == inst.raw
    cmd_bytes = [b for c in tune.commands for b in (c.lo, c.hi)]
    assert region(NP25_BASES["cmd"], len(cmd_bytes)) == cmd_bytes
    pulse = [
        b for s in tune.pw_program for b in (s.reset, s.step, s.dir_rate, s.next_off)
    ]
    assert region(NP25_BASES["pulse"], len(pulse)) == pulse
    filt = [
        b for s in tune.filter_program for b in (s.value, s.step, s.dwell, s.next_off)
    ]
    assert region(NP25_BASES["filter"], len(filt)) == filt
    assert region(NP25_BASES["pitch"], len(tune.pitch_table)) == tune.pitch_table

    # order lists are emitted verbatim (source bytes incl. $FF/$FE terminator).
    for voice, order in enumerate(tune.subtunes[0].order_lists):
        assert region(NP25_BASES[f"order{voice}"], len(order.raw)) == order.raw

    # seq lo/hi tables resolve to the verbatim, $7F-terminated source bytes.
    for index, raw in enumerate(tune.pattern_raw):
        seq = byte(NP25_BASES["seqlo"] + index) | (
            byte(NP25_BASES["seqhi"] + index) << 8
        )
        assert [byte(seq + i) for i in range(len(raw))] == raw
        assert raw[-1] == 0x7F


def test_write_read_prg_roundtrip():
    """``write_editor_prg`` -> ``read_editor_prg`` recovers the tune structures.

    The self-terminating music data (order lists, sequences) round-trips
    byte-for-byte with no out-of-band length; every terminatorless table reads
    back verbatim at its ``$0FA0`` pointer-block base.
    """
    tune = editor._split_long_patterns(_tune())
    profile = editor.NP20_PROFILE
    img = editor.read_editor_prg(
        editor.write_editor_prg(tune, driver=DRIVER, profile=profile), profile=profile
    )
    assert img.magic == profile.version_magic
    assert img.tempo == tune.subtunes[0].tempo
    assert img.order_lists == [list(o.raw) for o in tune.subtunes[0].order_lists]
    assert img.sequences == [list(raw) for raw in tune.pattern_raw]
    assert img.table(profile.idx_wave, len(tune.wavetable.note_col)) == (
        tune.wavetable.note_col
    )
    assert img.table(profile.idx_wave2, len(tune.wavetable.ctrl_col)) == (
        tune.wavetable.ctrl_col
    )
    assert img.table(profile.idx_pitch, len(tune.pitch_table)) == tune.pitch_table
    for index, inst in enumerate(tune.instruments):
        assert img.table(profile.idx_inst, (index + 1) * 8)[index * 8 :] == (
            list(inst.raw[:8])
        )


def test_read_editor_prg_rejects_wrong_magic():
    prg = editor.write_editor_prg(_tune(), driver=DRIVER, profile=editor.NP20_PROFILE)
    with pytest.raises(SidParseError):
        editor.read_editor_prg(prg, profile=editor.NP25_PROFILE)


def test_read_editor_prg_rejects_truncated():
    with pytest.raises(SidParseError):
        editor.read_editor_prg(b"\x00", profile=editor.NP20_PROFILE)


def test_default_profile_follows_tune_version():
    tune = _tune()
    assert editor.profile_for_tune(tune) is editor.NP20_PROFILE
    prg = editor.write_editor_prg(tune, driver=DRIVER)
    _load, _word, byte = _image(prg)
    assert bytes(byte(editor.NP20_PROFILE.ptr_magic + i) for i in range(5)) == b"20.G4"


@pytest.mark.parametrize(
    "profile",
    [
        editor.NP21_PROFILE,
        editor.NP22_PROFILE,
        editor.NP23_PROFILE,
        editor.NP24_PROFILE,
        editor.NP25_PROFILE,
    ],
)
def test_cross_version_export_refused(profile):
    with pytest.raises(SidParseError, match="cannot export"):
        editor.write_editor_prg(_tune(), driver=DRIVER, profile=profile)


def test_family_tune_has_no_profile():
    family = _tune()
    family.provenance = dataclasses.replace(
        family.provenance, tier="family", version="V15/V18-class"
    )
    with pytest.raises(SidParseError, match="no editor profile"):
        editor.write_editor_prg(family, driver=DRIVER)
    with pytest.raises(SidParseError, match="no editor profile"):
        editor.write_editor_prg(family, driver=DRIVER, profile=editor.NP25_PROFILE)


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
        editor.write_editor_prg(over, driver=DRIVER)
    # >114 sequences: over the editor pattern cap.
    many = dataclasses.replace(
        tune, patterns=[[]] * 115, pattern_raw=[[0x01, 0x7F]] * 115
    )
    with pytest.raises(SidParseError, match="patterns"):
        editor.write_editor_prg(many, driver=DRIVER)
    # An over-96-row sequence with no $7F terminator cannot be split -> rejected.
    unterminated = dataclasses.replace(
        tune,
        patterns=[[]],
        pattern_raw=[[0x01] * 200],
        subtunes=[dataclasses.replace(tune.subtunes[0])],
    )
    with pytest.raises(SidParseError, match="rows"):
        editor.write_editor_prg(unterminated, driver=DRIVER)


def test_long_pattern_split_roundtrip():
    """A >96-row sequence splits losslessly into <=96-row chunks on export.

    The emitted order list references the chunk sequences in order; walking it
    through the seq-pointer tables and concatenating the chunk bodies reproduces
    the original recovered sequence bytes exactly (minus injected $7F).
    """
    tune = _tune()
    profile = editor.NP20_PROFILE
    # A 150-row voice-0 sequence: 150 note bytes ($01), $7F terminator.
    body = [0x01] * 150
    long_raw = body + [0x7F]
    events = [dataclasses.replace(tune.patterns[0][1]) for _ in range(150)]
    patterns = list(tune.patterns)
    pattern_raw = list(tune.pattern_raw)
    patterns[0] = events
    pattern_raw[0] = long_raw
    sub = tune.subtunes[0]
    order0 = dataclasses.replace(
        sub.order_lists[0],
        entries=[editor.OrderEntry(pattern=0, transpose=0)],
        raw=[0x00, 0xFF],
    )
    subtune = dataclasses.replace(sub, order_lists=[order0] + sub.order_lists[1:])
    tune = dataclasses.replace(
        tune, patterns=patterns, pattern_raw=pattern_raw, subtunes=[subtune]
    )

    prg = editor.write_editor_prg(tune, driver=DRIVER, profile=profile)
    _load, word, byte = _image(prg)

    seqlo = word(profile.ptr_addr(profile.idx_seqlo))
    seqhi = word(profile.ptr_addr(profile.idx_seqhi))

    def seq_body(idx):
        addr = byte(seqlo + idx) | (byte(seqhi + idx) << 8)
        out = []
        while byte(addr) != 0x7F:
            out.append(byte(addr))
            addr += 1
        return out

    order_base = word(profile.ptr_addr(profile.idx_order[0]))
    addr = order_base
    seqs = []
    while byte(addr) not in (0xFE, 0xFF):
        if byte(addr) < 0x80:
            seqs.append(byte(addr))
        addr += 1
    assert len(seqs) >= 2  # 150 rows -> at least two <=96-row chunks
    rebuilt = [b for idx in seqs for b in seq_body(idx)]
    assert rebuilt == body
    for idx in seqs:
        assert len(seq_body(idx)) <= 96  # each chunk within the 96-row cap


def test_missing_table_raises_clear_message():
    tune = _tune()
    no_wave = dataclasses.replace(tune, wavetable=None)
    with pytest.raises(SidParseError, match="wave table"):
        editor.write_editor_prg(no_wave, driver=DRIVER)
    no_pitch = dataclasses.replace(tune, pitch_table=[])
    with pytest.raises(SidParseError, match="pitch table"):
        editor.write_editor_prg(no_pitch, driver=DRIVER)


def test_multi_subtune_roundtrip():
    """Three subtunes survive write -> read; the block ends with a double mark."""
    tune = editor._split_long_patterns(_tune())
    base = tune.subtunes[0]
    subs = []
    for k in range(3):
        orders = []
        for order in base.order_lists:
            raw = list(order.raw)
            raw[0] = 0x80 + k  # distinct transpose per subtune
            orders.append(dataclasses.replace(order, raw=raw))
        subs.append(dataclasses.replace(base, order_lists=orders))
    tune = dataclasses.replace(tune, subtunes=subs)
    profile = editor.NP20_PROFILE
    img = editor.read_editor_prg(
        editor.write_editor_prg(tune, driver=DRIVER, profile=profile), profile=profile
    )
    assert len(img.tunes) == 3
    for k, sub in enumerate(subs):
        for voice, order in enumerate(sub.order_lists):
            assert img.tunes[k][voice] == editor._mark_pair(order.raw)
    assert img.order_lists == [list(o.raw) for o in subs[0].order_lists]


def test_empty_driver_raises():
    with pytest.raises(SidParseError, match="driver"):
        editor.write_editor_prg(_tune(), driver=b"")


def test_short_driver_raises():
    with pytest.raises(SidParseError, match="too short"):
        editor.write_editor_prg(_tune(), driver=DRIVER[:0x20])


def test_bases_follow_driver_np20_layout():
    """Tables land at the driver's bases (NP20 differs from NP22/23/25)."""
    tune = editor._split_long_patterns(_tune())
    profile = editor.NP20_PROFILE
    driver = make_driver(NP20_BASES, profile)
    prg = editor.write_editor_prg(tune, driver=driver, profile=profile)
    load, word, byte = _image(prg)
    # driver bytes below the first table, incl. the pointer block, survive.
    assert prg[2 : 2 + len(driver)][:0x40] == driver[:0x40]
    assert word(profile.ptr_addr(profile.idx_pitch)) == NP20_BASES["pitch"]
    pitch = [byte(NP20_BASES["pitch"] + i) for i in range(len(tune.pitch_table))]
    assert pitch == tune.pitch_table
    img = editor.read_editor_prg(prg, profile=profile)
    assert img.order_lists == [list(o.raw) for o in tune.subtunes[0].order_lists]
    assert img.sequences == [list(raw) for raw in tune.pattern_raw]
    assert img.tempo == tune.subtunes[0].tempo
    assert byte(NP20_BASES["init"] + 7) == 0  # +7 left alone
    # nothing below the first table base differs from the driver (but the magic).
    body = prg[2:]
    first = NP20_BASES["pitch"] - load
    assert body[:first] == bytes(
        driver[i] if not 0xEE <= i < 0xF3 else profile.version_magic[i - 0xEE]
        for i in range(first)
    )


def test_table_overflowing_next_base_raises():
    tune = _tune()
    tight = dict(NP25_BASES, finetune=NP25_BASES["pitch"] + 2)
    with pytest.raises(SidParseError, match="pitch table overflows"):
        editor.write_editor_prg(tune, driver=make_driver(tight))


def _work_file(magic):
    """A written NP20 work file whose ``$0FEE`` magic is replaced by ``magic``."""
    prg = bytearray(
        editor.write_editor_prg(_tune(), driver=DRIVER, profile=editor.NP20_PROFILE)
    )
    start = 2 + editor.NP20_PROFILE.ptr_magic - editor.NP20_PROFILE.load_addr
    prg[start : start + 5] = magic
    return bytes(prg)


@pytest.mark.parametrize(
    "magic", [b"20.G4", b"21.G4", b"21.B4", b"21.G6", b"10.G1", b"17.Q0", b"04.13"]
)
def test_read_detects_any_nn_magic(magic):
    tune = _tune()
    img = editor.read_editor_prg(_work_file(magic))
    assert img.magic == magic
    assert img.version == magic.decode("ascii")
    assert img.order_lists == [list(o.raw) for o in tune.subtunes[0].order_lists]
    assert img.sequences == [list(raw) for raw in tune.pattern_raw]


def test_read_magicless_work_file_uses_name():
    prg = _work_file(bytes(5))
    assert editor.read_editor_prg(prg).version is None
    assert editor.read_editor_prg(prg, name="GROOVY (4)/05.02").version == "05.02"
    assert editor.read_editor_prg(prg, name="untitled").version is None
    assert editor.version_from_name("WORK/SWEET (2)/06.01") == "06.01"


def test_read_rejects_non_work_file_magic():
    with pytest.raises(SidParseError, match="not an editor work file"):
        editor.read_editor_prg(_work_file(b"hello"))


def test_read_editor_prg_is_public_api():
    import pyjch

    assert pyjch.read_editor_prg is editor.read_editor_prg
    assert pyjch.EditorImage is editor.EditorImage
    assert pyjch.profile_for_magic(b"25.FX") is editor.NP25_PROFILE
