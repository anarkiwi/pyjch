"""V0x stream grammar: the bytes it names behave as ``docs/format.md`` says."""

import pytest
from pysidtracker.playspec import compile_program, elaborate
from pysidtracker.playspec.pyexec import controls

from pyjch import grammar, reader
from pyjch.cli_plugin import FORMAT
from pyjch.errors import SidParseError
from pyjch.player import JchPlayer, spec, v0x_layout
from pyjch.model import Song
from pysidtracker.testing import whole_song

from tests.tunes import V0X_REFERENCES

LOAD = 0x1000
ORDERS = (0x1A00, 0x1A10, 0x1A20)
SUBPATTERN = 0x1900
CALLS = whole_song(300)


def _memory(order, subpattern):
    """A V0x image: three voices running ``order`` and subpattern 0 ``subpattern``."""
    mem = bytearray(0x10000)
    for index in range(0x60):
        mem[LOAD + 0x21F + 2 * index] = index
        mem[LOAD + 0x220 + 2 * index] = 0x10 + index
    for addr in ORDERS:
        mem[addr : addr + len(order)] = order
    mem[0x1800] = SUBPATTERN & 0xFF
    mem[0x1880] = SUBPATTERN >> 8
    mem[SUBPATTERN : SUBPATTERN + len(subpattern)] = subpattern
    return mem


def _layout():
    """The binding of the synthetic V0x tune: every voice starts at its order list's first byte."""
    return {
        "load": LOAD,
        "subptr_lo": 0x1800,
        "subptr_hi": 0x1880,
        "gate_ctrl": 0x41,
        "gateoff_ctrl": 0x40,
        "init_tempo": 1,
        "init_speed": 3,
        "init_left": [0xFF] * 3,
        "init_beats": [3] * 3,
        "init_transpose": [0] * 3,
        "init_inside": [0] * 3,
        "init_shift": [0] * 3,
        "init_detune": [0] * 3,
        "init_freq": [0] * 3,
        "init_base": list(ORDERS),
        "init_cur": list(ORDERS),
    }


def _trace(order, subpattern):
    """The SID registers and where each voice has read to (order list, subpattern) per call."""
    program = elaborate(spec("v0x"), _layout())
    mem = _memory(bytes(order), bytes(subpattern))
    frame, state = compile_program(program), controls(program)
    slots = {program.keys[name]: i for i, (name, _w, _d) in enumerate(program.controls)}
    where = [
        slots[f"voice[{v}].{cell}"] for v in range(3) for cell in ("orderpos", "subpos")
    ]
    out = []
    for _ in range(CALLS):
        frame(mem.__getitem__, mem.__setitem__, state, None, {})
        out.append((bytes(mem[0xD400:0xD419]), tuple(state[i] for i in where)))
    return out


BODY = [0x85, 0x00]
NOTES = [0x83, 0x30, 0x00, 0x31, 0x00]


@pytest.mark.parametrize("end", [0xA0, 0xC3, 0xFF])
def test_any_byte_from_a0_restarts_the_order_list(end):
    assert _trace(BODY + [end], NOTES + [0xFF]) == _trace(BODY + [0xFF], NOTES + [0xFF])


def test_a_byte_below_a0_does_not_restart_the_order_list():
    """``$9F`` only sets the transpose: the list runs on to the next subpattern."""
    assert _trace(BODY + [0x9F, 0x00, 0xFF], NOTES + [0xFF]) != _trace(
        BODY + [0xFF], NOTES + [0xFF]
    )


def test_the_order_list_really_restarts():
    pointers = [pointer for _regs, pointer in _trace(BODY + [0xFF], NOTES + [0xFF])]
    start = pointers[0]
    assert start in pointers[1:] and len(set(pointers)) > 1


@pytest.mark.parametrize("end", [0x90, 0xB7, 0xFF])
def test_any_byte_from_90_returns_from_the_subpattern(end):
    assert _trace(BODY + [0xFF], NOTES + [end]) == _trace(BODY + [0xFF], NOTES + [0xFF])


def test_a_duration_byte_is_read_in_the_same_call_and_outlasts_the_subpattern():
    """``$8F`` before the return stretches the next notes: it did not end the stream."""
    assert _trace(BODY + [0xFF], [0x30, 0x00, 0x8F, 0xFF]) != _trace(
        BODY + [0xFF], [0x30, 0x00, 0xFF]
    )


def _advances(trace):
    pointers = [pointer for _regs, pointer in trace]
    return sum(a != b for a, b in zip(pointers, pointers[1:]))


def test_a_longer_duration_advances_the_voice_less_often():
    fast = _trace(BODY + [0xFF], [0x81, 0x30, 0x31, 0x32, 0xFF])
    slow = _trace(BODY + [0xFF], [0x8F, 0x30, 0x31, 0x32, 0xFF])
    assert _advances(fast) > 4 * _advances(slow)


@pytest.mark.parametrize("relpath", V0X_REFERENCES)
def test_every_stream_of_a_real_tune_parses_and_reads_back(relpath, hvsc):
    song = reader.read(hvsc(relpath))
    assert isinstance(song, Song)
    streams = list(FORMAT.streams(song))
    assert [name for name, _model, _rule in streams][:3] == [
        f"order list {song.subtune}.{voice}" for voice in range(3)
    ]
    assert any(rule == grammar.V0X_SUBPATTERN for _name, _model, rule in streams)
    grammar.GRAMMAR.check(streams, SidParseError)


def test_the_listing_shows_the_bytes_by_role(hvsc):
    lines = list(FORMAT.listing(reader.read(hvsc(V0X_REFERENCES[0]))))
    assert any("V0X_TRANSPOSE=$8" in line for line in lines)
    assert any("V0X_DURATION=$8" in line for line in lines)
    assert any(line.endswith("V0X_RETURN=$ff") for line in lines)


def test_the_synthetic_layout_matches_the_song_binding(hvsc):
    song = reader.read(hvsc(V0X_REFERENCES[0]))
    layout = v0x_layout(song, JchPlayer(song).view())
    assert set(layout) == set(_layout())
    assert elaborate(spec("v0x"), layout).name == "v0x"
