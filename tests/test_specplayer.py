"""The V20 playspec: static checks, and the grid it plays on the synthetic tune."""

import hashlib
from pathlib import Path

import numpy as np
import pytest
from pysidtracker import EmuPlayer
from pysidtracker.playspec import elaborate, parse
from pysidtracker.playspec.tv import replay, validate
from pysidtracker.testing import song_frames, whole_song

import pyjch
from pyjch import reader
from pyjch.newplayer import abs_tokens, find_site
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


V20_TUNES = {name: rel for name, rel in TUNES.items() if name.startswith("v20")}
# Where each proved routine begins in the 6502 code: its first instructions, with the
# per-voice work cells (offsets from the code base) written as operands.
ROUTINES = {
    "inst_init": "BC {instn} B9 {{op:w}} 9D {wtptr}",
    "blit": "A9 00 9D {portapend} 9D {viben2} 9D {vibsup}",
}
CELLS = {
    "instn": 0x01D,
    "wtptr": 0x795,
    "portapend": 0x760,
    "viben2": 0x763,
    "vibsup": 0x766,
}


def _routine(hvsc, relpath, name):
    """``(model, image, pc)``: the tune and where its ``name`` code begins.

    From there the 6502 code runs to the play routine's final ``RTS``.
    """
    model = reader.parse(hvsc(relpath).read_bytes())
    cells = {cell: abs_tokens(model.init_addr + off) for cell, off in CELLS.items()}
    site = find_site(model.sid, ROUTINES[name].format(**cells))
    image = bytearray(0x10000)
    image[model.load_addr : model.load_addr + len(model.image)] = model.image
    return model, bytes(image), site.addr


@pytest.mark.oracle
@pytest.mark.parametrize("name", ROUTINES)
@pytest.mark.parametrize("relpath", V20_TUNES.values(), ids=list(V20_TUNES))
def test_a_routine_is_proved_equal_to_the_6502_code(name, relpath, hvsc):
    """For all initial memory, ``voice[0].<name>`` leaves what the 6502 code leaves."""
    model, image, pc = _routine(hvsc, relpath, name)
    layout = v20_layout(model, playable(model))
    result = validate(
        spec("newplayer"), layout, f"voice[0].{name}", image, pc, registers={"X": 0}
    )
    assert result.proven, result.counterexample


@pytest.mark.oracle
def test_a_changed_spec_is_refuted_and_the_counterexample_replays(hvsc):
    model, image, pc = _routine(hvsc, V20_TUNES["v20_7d_funkt"], "inst_init")
    layout = v20_layout(model, playable(model))
    text = (Path(pyjch.__file__).parent / "specs" / "newplayer.play").read_text()
    broken = parse(
        text.replace(
            "sid[u16(stride) + 4] := L.hardrestart;",
            "sid[u16(stride) + 4] := L.hardrestart ^ 1;",
        )
    )
    result = validate(
        broken, layout, "voice[0].inst_init", image, pc, registers={"X": 0}
    )
    assert not result.proven
    cex = result.counterexample
    diffs = replay(broken, layout, "voice[0].inst_init", cex["memory"], pc, {"X": 0})
    assert diffs == [cex["address"]]


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


def test_a_pattern_without_a_terminator_is_reported():
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
    validator = _validated(reader.parse(data), song_frames(data, None))
    assert validator.results
    assert validator.failures() == {}
