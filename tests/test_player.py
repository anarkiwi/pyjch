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
  tune's own driver run on py65).

These assert the shared :class:`~pysidtracker.MemPlayer` contract (grid shape,
first-frame-all-registers, changed-only diffs, determinism) and the version
dispatch -- byte-exactness is validated in ``tests/test_oracle_hvsc.py``.
"""

import pytest

from pyjch import reader
from pyjch.errors import SidParseError
from pyjch.model import Song
from pyjch.newplayer import NewPlayerModel
from pyjch.player import JchPlayer, iter_frames, playable, render_grid

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
    assert player._version == "v0x"  # pylint: disable=protected-access
    assert isinstance(reader.read(tune_path), Song)


def test_v20_model_selects_v20():
    """A playable NewPlayerModel drives the V20 engine."""
    model = _v20_model()
    assert isinstance(model, NewPlayerModel)
    assert playable(model) is not None
    assert JchPlayer(model)._version == "v20"  # pylint: disable=protected-access


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
    assert JchPlayer(model)._version == "v20"  # pylint: disable=protected-access


@pytest.mark.parametrize(
    "idiom",
    [
        b"\x9d\x5d\x17",  # wave-ctrl store
        b"\x8d\x46\x17",  # filter-program store
        b"\x9d\x81\x17",  # PW-program store
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
    assert JchPlayer(model)._version == "emu"  # pylint: disable=protected-access
