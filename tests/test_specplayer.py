"""The V20 playspec: static checks, and the grid it plays on the synthetic tune."""

import hashlib

import numpy as np
from pysidtracker.playspec import elaborate
from pysidtracker.testing import whole_song

from pyjch import reader
from pyjch.player import make_player, playable, spec, v20_layout

from tests import _synth_v20 as synth

# Digest of the grid the hand-written V20 engine played on this image over
# SYNTH_FRAMES calls, many passes of every order list.
SYNTH_GRID_DIGEST = "314207ef13679a7c70b68514fd1732c8"
SYNTH_FRAMES = whole_song(6000)


def _model():
    return reader.parse(synth.build_psid())


def test_spec_elaborates_against_the_synthetic_layout():
    model = _model()
    program = elaborate(spec(), v20_layout(model, playable(model)))
    assert program.name == "newplayer"


def test_spec_plays_the_grid_the_hand_written_engine_played():
    grid = np.asarray(make_player(_model()).render_grid(SYNTH_FRAMES), np.uint8)
    assert hashlib.blake2b(grid.tobytes(), digest_size=16).hexdigest() == (
        SYNTH_GRID_DIGEST
    )
