"""Unit tests for the single :class:`~pyjch.player.JchPlayer`.

One player hosts every recovered JCH driver version, version-selected from the
model type:

* **V0x** from a :class:`~pyjch.model.Song`, exercised against a real cached
  reference tune (skipped offline; byte-exact validation is in
  ``tests/test_oracle_hvsc.py``);
* **V20** from a :class:`~pyjch.newplayer.NewPlayerModel`, exercised offline
  against the hand-authored synthetic image (:mod:`tests._synth_v20`), which is
  crafted to drive every engine path (fetch / commit / instrument-init /
  portamento / vibrato / filter / patch / blit);
* every other recovered family build via :class:`~pysidtracker.EmuPlayer` (the
  tune's own driver run on jennings), which :func:`~pyjch.player.make_player`
  returns.

These assert the shared :class:`~pysidtracker.MemPlayer` contract (grid shape,
first-frame-all-registers, changed-only diffs, determinism) and the version
dispatch -- byte-exactness is validated in ``tests/test_oracle_hvsc.py``.
"""

import pytest
from pysidtracker import EmuPlayer

from pyjch import reader
from pyjch.errors import SidParseError
from pyjch.model import Song
from pyjch.newplayer import NewPlayerModel
from pyjch.player import (
    V20_CODE,
    JchPlayer,
    engine_of,
    iter_frames,
    make_player,
    playable,
    render_grid,
    v20_bases,
    v20_layout,
)

from tests import _synth_v20 as synth
from tests.tunes import V0X_REFERENCES

NREG = 25


def _v20_model():
    return reader.parse(synth.build_psid())


# --- version dispatch -------------------------------------------------------
@pytest.mark.parametrize("relpath", V0X_REFERENCES)
def test_v0x_model_selects_v0x(relpath, hvsc):
    """A Song drives the V0x routine."""
    tune_path = hvsc(relpath)
    player = JchPlayer(reader.read(tune_path))
    assert player.engine == "v0x"
    assert isinstance(reader.read(tune_path), Song)


def test_v20_model_selects_v20():
    """A playable NewPlayerModel is played by the V20 spec player."""
    model = _v20_model()
    assert isinstance(model, NewPlayerModel)
    assert playable(model) is not None
    player = make_player(model)
    assert isinstance(player, JchPlayer)
    assert engine_of(player) == "v20"


def test_unknown_model_type_rejected():
    with pytest.raises(SidParseError):
        JchPlayer(object())


# --- shared MemPlayer contract (both versions) ------------------------------
@pytest.mark.parametrize("frames", [1, 50, 200])
def test_v20_render_grid_shape_and_determinism(frames):
    grid = render_grid(_v20_model(), frames)
    assert len(grid) == frames
    assert all(len(row) == NREG for row in grid)
    assert grid == render_grid(_v20_model(), frames)


def test_v20_first_frame_all_registers_then_changed_only():
    frames = list(iter_frames(_v20_model(), max_frames=400))
    assert len(frames) == 400
    assert [reg for reg, _ in frames[0]] == list(range(NREG))
    assert any(len(frame) < NREG for frame in frames[1:])
    # a running tune keeps changing registers frame to frame
    assert sum(len(frame) for frame in frames) > 400


@pytest.mark.parametrize("relpath", V0X_REFERENCES)
def test_v0x_render_grid_shape_and_determinism(relpath, hvsc):
    tune_path = hvsc(relpath)
    song = reader.read(tune_path)
    rows = render_grid(song, 64)
    assert len(rows) == 64 and all(len(r) == NREG for r in rows)
    assert render_grid(song, 64) == render_grid(song, 64)


@pytest.mark.parametrize("relpath", V0X_REFERENCES)
def test_v0x_first_frame_all_registers(relpath, hvsc):
    tune_path = hvsc(relpath)
    writes = JchPlayer(reader.read(tune_path)).play_frame()
    assert [reg for reg, _ in writes] == list(range(NREG))


@pytest.mark.parametrize("relpath", V0X_REFERENCES)
def test_v0x_loops_forever_until_max_frames(relpath, hvsc):
    tune_path = hvsc(relpath)
    assert len(list(iter_frames(reader.read(tune_path), max_frames=500))) == 500


# --- playability gate -------------------------------------------------------
def test_hard_restart_immediate_selected_per_build():
    model = reader.parse(synth.build_psid(hard_restart=0x11))
    assert playable(model).hardrestart == 0x11
    assert v20_layout(model, playable(model))["hardrestart"] == 0x11


@pytest.mark.parametrize(
    "idiom",
    [
        b"\x9d\x5d\x17",  # wave-ctrl store
        b"\x8d\x46\x17",  # filter-program store
        b"\x9d\x81\x17",  # PW-program store
        b"\xa5\xfb\x48\xa5\xfc\x48",  # zero-page pointer save
        b"\x68\x85\xfc\x68\x85\xfb\x60",  # zero-page pointer restore
        b"\xc9\xff\xf0\x03\x8d\x92\x17",  # filter-program start
    ],
)
def test_non_v20_family_falls_back_to_emulation(idiom):
    """A recovered family model without the native V20 idioms plays via EmuPlayer."""
    image, _load = synth.build_image()
    buf = bytearray(image)
    pos = buf.index(idiom)
    buf[pos : pos + len(idiom)] = b"\xea" * len(idiom)
    header = bytearray(synth.build_psid()[:0x7C])
    model = reader.parse(bytes(header) + bytes(buf))
    assert isinstance(model, NewPlayerModel)
    assert playable(model) is None  # not the native V20 build
    player = make_player(model, subtune=1)
    assert type(player) is EmuPlayer  # pylint: disable=unidiomatic-typecheck
    assert engine_of(player) == "emu"
    reference = EmuPlayer(
        model.image, model.load_addr, model.init_addr, model.play_addr, 1
    )
    assert player.render_grid(8) == reference.render_grid(8)
    with pytest.raises(SidParseError, match="has no spec"):
        JchPlayer(model)


@pytest.mark.parametrize("relpath", V0X_REFERENCES)
def test_make_player_keeps_the_native_v0x_engine(relpath, hvsc):
    player = make_player(reader.read(hvsc(relpath)))
    assert isinstance(player, JchPlayer)
    assert engine_of(player) == player.engine == "v0x"


def test_native_snapshot_masks_pulse_width_high(hvsc):
    """The native engine nibble-masks PW-hi in the grid, as EmuPlayer does."""
    player = JchPlayer(reader.read(hvsc(V0X_REFERENCES[0])))
    for reg in (0x03, 0x0A, 0x11):
        player._wr(0xD400 + reg, 0xF0 | reg)  # pylint: disable=protected-access
    regs = player.snapshot()
    assert [regs[r] for r in (0x03, 0x0A, 0x11)] == [0x03, 0x0A, 0x01]


def test_parse_subtune_kwarg_selects_default_subtune():
    """``parse(..., subtune=k)`` (the ``BaseSidParser.read`` contract) is honoured."""
    data = synth.build_psid()
    parser = reader.JchSidParser()
    assert parser.read(data).subtune == 0
    with pytest.raises(SidParseError):
        parser.read(data, subtune=99)
    with pytest.raises(TypeError):
        parser.read(data, bogus=1)


def test_a_build_whose_code_differs_is_gated_out_by_name():
    image, _load = synth.build_image()
    buf = bytearray(image)
    idiom = b"\x68\x85\xfc\x68\x85\xfb\x60"
    pos = buf.index(idiom)
    buf[pos + 1] = 0x07  # PLA ; SLO $FC: the pointer is no longer restored
    model = reader.parse(bytes(synth.build_psid()[:0x7C]) + bytes(buf))
    assert v20_bases(model) == (None, V20_CODE)
