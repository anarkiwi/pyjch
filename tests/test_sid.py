"""decompile_sid: the core found by its own vectors, whatever drives it.

Real HVSC tunes (resolved through the ``hvsc`` fixture, never committed): a
plain PSID V20 build, the same core under a banked-I/O wrapper, and the
four-track sample build under its own raster interrupt.  Each is checked
against the original ``.sid`` run on jennings, frame by frame per register.
"""

import dataclasses

import pytest
from pysidtracker import EmuPlayer, PlayroutineKind, register_grid

from pyjch import JchPlayer, SidParseError, decompile_sid, extract
from pyjch.errors import JCHError

GULDKORN = "MUSICIANS/J/JCH/Guldkornekspressen_Intro.sid"
KNOB = "MUSICIANS/P/Puterman/I_Could_Eat_a_Knob_at_Night.sid"
EASY = "MUSICIANS/J/JCH/Easy_Does_It.sid"
FOREIGN = "MUSICIANS/H/Hubbard_Rob/Commando.sid"
MULTI = "MUSICIANS/L/Link/Galactyforce.sid"
PACKED = "MUSICIANS/S/Sidder/Super_Mario_Bros.sid"
FRAMES = 600


def _grid_matches_original(data, song):
    assert JchPlayer(song).render_grid(FRAMES) == register_grid(data, FRAMES)


def test_plain_v20_build_plays_native_and_matches(hvsc):
    data = hvsc.read(GULDKORN)
    dec = decompile_sid(data)
    info = dec.info
    assert (info.version, info.kind, info.host) == (
        "V20",
        PlayroutineKind.DIRECT,
        "psid",
    )
    assert (info.code_base, info.voices, info.native) == (0x1000, 3, True)
    assert info.wrapper is None and dec.header.name == "Guldkornekspressen Intro"
    assert info.bases.notes == 96 and info.bases.pwprog < info.bases.instruments
    _grid_matches_original(data, dec.layout)


def test_wrapped_core_is_found_under_its_host(hvsc):
    """The header enters a wrapper; the core is the pair the wrapper calls."""
    data = hvsc.read(KNOB)
    dec = decompile_sid(data)
    info, song = dec.info, dec.layout
    assert (song.init_addr, song.play_addr) == (0x0E00, 0x0E03)
    assert (info.host, info.code_base, info.native) == ("wrapper", 0x1000, False)
    assert info.bases is not None and info.bases.notes == 96
    w = info.wrapper
    regs = {
        k: w[k] - w["buf"]
        for k in ("pw0_lo", "pw1_lo", "cut_reg", "res_reg", "vol_reg")
    }
    assert regs == {
        "pw0_lo": 2,
        "pw1_lo": 9,
        "cut_reg": 0x16,
        "res_reg": 0x17,
        "vol_reg": 0x18,
    }
    assert w["delay"] == w["flush_delay"]  # the record's last byte orders the flush
    _grid_matches_original(data, song)


def test_native_engine_matches_the_wrapped_core_on_its_own(hvsc):
    """Driven by its own vectors, the wrapped core is a plain V20 build."""
    song = decompile_sid(hvsc.read(KNOB)).layout
    core = dataclasses.replace(
        song, init_addr=song.code_base, play_addr=song.code_base + 3
    )
    player = JchPlayer(core)
    assert player._version == "v20"  # pylint: disable=protected-access
    emu = EmuPlayer(core.image, core.load_addr, core.init_addr, core.play_addr)
    assert player.render_grid(FRAMES) == emu.render_grid(FRAMES)


def test_four_track_sample_build_reads_under_its_interrupt(hvsc):
    dec = decompile_sid(hvsc.read(EASY))
    info, song = dec.info, dec.layout
    assert (info.version, info.host, info.code_base, info.voices) == (
        "V20",
        "irq",
        0x4000,
        4,
    )
    assert (song.record, song.tempo_offset, song.subtune_tempo(0)) == (16, 14, 2)
    assert info.bases is None and not info.native
    tracks = dec.song.subtunes[0].tracks
    assert len(tracks) == 4 and all(t.ops for t in tracks)
    assert len(extract(song).subtunes[0].order_lists) == 4
    grid = JchPlayer(song).render_grid(50)
    assert any(row[4] or row[11] or row[18] for row in grid)  # the score keys voices


def test_each_core_of_a_multi_core_image_is_the_one_its_song_enters(hvsc):
    """One core per song: the init dispatches on the song number."""
    data = hvsc.read(MULTI)
    bases = []
    for subtune in range(3):
        dec = decompile_sid(data, subtune)
        bases.append(dec.info.code_base)
        grid = JchPlayer(dec.layout, subtune).render_grid(FRAMES)
        assert grid == register_grid(data, FRAMES, subtune=subtune)
    assert bases == [0x5000, 0x3000, 0x4000]


def test_crunched_tune_is_read_after_its_init(hvsc):
    data = hvsc.read(PACKED)
    dec = decompile_sid(data)
    assert dec.info.kind is PlayroutineKind.PACKED and dec.info.native
    assert dec.layout.init_addr == dec.info.code_base
    _grid_matches_original(data, dec.layout)


def test_foreign_player_is_refused(hvsc):
    with pytest.raises(SidParseError):
        decompile_sid(hvsc.read(FOREIGN))
    with pytest.raises(JCHError):
        decompile_sid(b"PSID")
