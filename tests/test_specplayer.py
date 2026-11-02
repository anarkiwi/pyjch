"""The V20 playspec: static checks, and the grid it plays on the synthetic tune."""

import hashlib

import numpy as np
import pytest
from pysidtracker import EmuPlayer
from pysidtracker.playspec import elaborate
from pysidtracker.testing import song_frames, whole_song

import pyjch
from pyjch import reader
from pyjch.cli_plugin import FORMAT
from pyjch.player import (
    JchPlayer,
    make_player,
    play_variables,
    playable,
    spec,
    v20_layout,
    v0x_layout,
)

from tests import _synth_v20 as synth
from tests.test_oracle_hvsc import TUNES

SPEC_TUNES = {
    name: rel for name, rel in TUNES.items() if name.startswith(("v0x", "v20"))
}

# Digest of the grid the hand-written V20 engine played on this image over
# SYNTH_FRAMES calls, many passes of every order list.
SYNTH_GRID_DIGEST = "5fcf08ec2942390031fc4bd785a5ad74"
SYNTH_FRAMES = whole_song(6000)


def _model():
    return reader.parse(synth.build_psid())


def _layout(model):
    return v20_layout(model, playable(model), JchPlayer(model).view())


def test_spec_elaborates_against_the_synthetic_layout():
    model = _model()
    program = elaborate(spec("newplayer"), _layout(model))
    assert program.name == "newplayer"


def test_v0x_spec_elaborates_against_a_song_layout(hvsc):
    song = reader.read(hvsc(TUNES["v0x_flexible"]))
    program = elaborate(spec("v0x"), v0x_layout(song, JchPlayer(song).view()))
    assert program.name == "v0x"


def test_spec_plays_the_grid_the_hand_written_engine_played():
    grid = np.asarray(make_player(_model()).render_grid(SYNTH_FRAMES), np.uint8)
    assert hashlib.blake2b(grid.tobytes(), digest_size=16).hexdigest() == (
        SYNTH_GRID_DIGEST
    )


def test_format_exports_the_spec():
    model = _model()
    bound = FORMAT.spec(model)
    assert bound.spec is spec("newplayer")
    view = JchPlayer(model).view()
    assert elaborate(bound.spec, bound.bind(view, None)).name == "newplayer"
    assert FORMAT.spec_player(model).render_grid(SYNTH_FRAMES) == (
        make_player(model).render_grid(SYNTH_FRAMES)
    )


def test_format_has_no_spec_for_an_emulated_build(monkeypatch):
    monkeypatch.setattr("pyjch.player.playable", lambda model: None)
    with pytest.raises(pyjch.errors.SidParseError):
        FORMAT.spec(_model())


def test_the_play_variables_are_the_specs_state():
    """Every state the 6502 keeps in its work area is the spec's, and the spec adds only
    the positions of its cursors and the orderlist restart."""
    model = _model()
    player = JchPlayer(model)
    player.play_frame()
    named = {name for name, _ in play_variables(playable(model), 0, lambda _addr: 0)}
    states = set(player.program().keys.values())
    assert named <= states
    assert {name.split(".")[-1] for name in states - named} == {
        "start",
        "loop",
        "orderpos",
        "item",
    }


def _state_differences(model, calls):
    """The first call at which the spec's state differs from the work area of the tune's own
    6502 run, with the variables that differ."""
    player = make_player(model)
    if not isinstance(player, JchPlayer):
        pytest.skip("the spec refuses this tune")
    emu = EmuPlayer(
        model.image, model.load_addr, model.init_addr, model.play_addr, model.subtune
    )
    bases = playable(model)
    base = model.init_addr
    for call in range(calls):
        player.play_frame()
        emu.play_frame()
        theirs = emu._mem  # pylint: disable=protected-access
        wrong = {
            name: (want, player.state(name))
            for name, want in play_variables(bases, base, theirs.__getitem__)
            if player.state(name) != want
        }
        if wrong:
            return call, wrong
    return None


@pytest.mark.oracle
@pytest.mark.parametrize("relpath", SPEC_TUNES.values(), ids=list(SPEC_TUNES))
def test_the_spec_state_is_the_6502_work_area_after_every_call(relpath, hvsc):
    data = hvsc(relpath).read_bytes()
    model = reader.parse(data)
    if not isinstance(model, pyjch.newplayer.NewPlayerModel):
        pytest.skip("the V0x routine keeps its variables apart from the work area")
    assert _state_differences(model, song_frames(data, None)) is None


PATTERN_RULE = "pyjch.grammar:GRAMMAR:pattern"
ORDER_RULE = "pyjch.grammar:GRAMMAR:orderlist"
WAVE_RULE = "pyjch.grammar:GRAMMAR:wave_program"


def _validated(model, calls):
    player = JchPlayer(model, validate=True)
    player.render_grid(calls)
    return player.validator


def test_every_stream_the_spec_opens_on_the_synthetic_tune_is_well_formed():
    validator = _validated(_model(), whole_song(SYNTH_FRAMES))
    assert {ref for ref, _base in validator.results} == {
        PATTERN_RULE,
        ORDER_RULE,
        WAVE_RULE,
    }
    assert validator.failures() == {}


def test_a_pattern_without_a_terminator_is_reported(monkeypatch):
    monkeypatch.setattr("pyjch.refusal.refusal", lambda model, bases: None)
    data = bytearray(synth.build_psid())
    start = len(data) - (synth.IMAGE_LEN - (synth.PATTERNS - synth.LOAD))
    data[start : start + 0x100] = bytes([0x30]) * 0x100
    failures = _validated(
        reader.parse(bytes(data)), whole_song(SYNTH_FRAMES)
    ).failures()
    assert (PATTERN_RULE, synth.PATTERNS) in failures
    assert {ref for ref, _base in failures} == {PATTERN_RULE}
    assert all("no terminator" in message for message in failures.values())


@pytest.mark.oracle
@pytest.mark.parametrize("relpath", SPEC_TUNES.values(), ids=list(SPEC_TUNES))
def test_every_stream_a_real_tune_opens_matches_the_song_grammar(relpath, hvsc):
    data = hvsc(relpath).read_bytes()
    model = reader.parse(data)
    if not isinstance(make_player(model), JchPlayer):
        pytest.skip("the spec refuses this tune")
    validator = _validated(model, song_frames(data, None))
    assert validator.results
    assert validator.failures() == {}
