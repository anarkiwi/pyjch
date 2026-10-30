"""The V20 playspec: static checks, and the grid it plays on the synthetic tune."""

import hashlib

import numpy as np
import pytest
from pysidtracker import EmuPlayer
from pysidtracker.playspec import elaborate
from pysidtracker.testing import song_frames, whole_song

from pyjch import reader
from pyjch.player import JchPlayer, make_player, playable, spec, v20_layout, v0x_layout

from tests import _synth_v20 as synth
from tests.test_oracle_hvsc import TUNES

STACK = slice(0x100, 0x200)
SPEC_TUNES = {
    name: rel for name, rel in TUNES.items() if name.startswith(("v0x", "v20"))
}

# Digest of the grid the hand-written V20 engine played on this image over
# SYNTH_FRAMES calls, many passes of every order list.
SYNTH_GRID_DIGEST = "314207ef13679a7c70b68514fd1732c8"
SYNTH_FRAMES = whole_song(6000)


def _model():
    return reader.parse(synth.build_psid())


def test_spec_elaborates_against_the_synthetic_layout():
    model = _model()
    program = elaborate(spec("newplayer"), v20_layout(model, playable(model)))
    assert program.name == "newplayer"


def test_v0x_spec_elaborates_against_a_song_layout(hvsc):
    song = reader.read(hvsc(TUNES["v0x_flexible"]))
    program = elaborate(spec("v0x"), v0x_layout(song))
    assert program.name == "v0x"


def test_spec_plays_the_grid_the_hand_written_engine_played():
    grid = np.asarray(make_player(_model()).render_grid(SYNTH_FRAMES), np.uint8)
    assert hashlib.blake2b(grid.tobytes(), digest_size=16).hexdigest() == (
        SYNTH_GRID_DIGEST
    )


@pytest.mark.oracle
@pytest.mark.parametrize("relpath", SPEC_TUNES.values(), ids=list(SPEC_TUNES))
def test_memory_equals_the_6502_run_after_every_call(relpath, hvsc):
    """Outside the stack page the 64 KiB is the tune's own 6502 run, each call."""
    data = hvsc(relpath).read_bytes()
    model = reader.parse(data)
    player = make_player(model)
    assert isinstance(player, JchPlayer)
    emu = EmuPlayer(
        model.image, model.load_addr, model.init_addr, model.play_addr, model.subtune
    )
    for call in range(song_frames(data, None)):
        player.play_frame()
        emu.play_frame()
        ours = bytearray(player.memory)
        theirs = bytearray(emu._mem)  # pylint: disable=protected-access
        ours[STACK] = theirs[STACK] = bytes(0x100)
        if ours != theirs:
            where = next(a for a in range(0x10000) if ours[a] != theirs[a])
            pytest.fail(
                f"call {call}: ${where:04X} is {ours[where]:#04x}, "
                f"the 6502 left {theirs[where]:#04x}"
            )
