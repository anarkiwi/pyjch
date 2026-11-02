"""The V20 tunes the spec refuses: the facts it refuses them by, and the HVSC tunes that are."""

import dataclasses
from pathlib import Path

import pytest

from pyjch import reader
from pyjch.errors import SidParseError
from pyjch.constants import OVERLAP, SPEC_REFUSALS, VOICE_CELLS, WILD_KINDS
from pysidtracker.playspec import SpecPlayer
from pyjch.player import (
    JchPlayer,
    spec_binding,
    pitch_tail,
    engine_of,
    make_player,
    spec_gate,
    v0x_refused,
    v20_bases,
)
from pyjch.refusal import refusal
from pysidtracker import EmuPlayer

from tests import _synth_v20 as synth
from tests.tunes import V0X_REFERENCES

REFUSED = [
    tuple(line.split("\t"))
    for line in (Path(__file__).parent / "fixtures" / "jch_refused.txt")
    .read_text(encoding="utf-8")
    .splitlines()
    if line and not line.startswith("#")
]
CELLS = 0x1000  # the code base of the synthetic tune


def _model(patches=()):
    """The synthetic tune with ``(address, bytes)`` patches applied to its image."""
    data = bytearray(synth.build_psid())
    start = len(data) - synth.IMAGE_LEN
    for addr, blob in patches:
        at = start + addr - synth.LOAD
        data[at : at + len(blob)] = blob
    return reader.parse(bytes(data))


def test_the_synthetic_tune_is_played_by_the_spec():
    model = _model()
    assert spec_gate(model)[1] == "reference"
    assert isinstance(make_player(model), JchPlayer)


def test_a_voice_whose_registers_are_not_seven_apart_is_refused():
    model = _model([(CELLS + 0x740 + 2, bytes([15]))])
    assert spec_gate(model) == (None, VOICE_CELLS)
    assert (
        type(make_player(model, 0)) is EmuPlayer
    )  # pylint: disable=unidiomatic-typecheck


def test_a_table_over_a_variable_is_refused():
    model = _model()
    bases = dataclasses.replace(v20_bases(model)[0], cmdparam=CELLS + 0x747)
    assert refusal(model, bases) == OVERLAP


def test_a_v0x_voice_whose_registers_are_not_seven_apart_is_refused(hvsc):
    song = reader.read(hvsc(V0X_REFERENCES[0]))
    assert not v0x_refused(song)
    image = bytearray(song.image)
    image[0x2F1 + 1] = 15
    moved = dataclasses.replace(song, image=bytes(image))
    assert v0x_refused(moved)
    assert (
        type(make_player(moved)) is EmuPlayer
    )  # pylint: disable=unidiomatic-typecheck
    with pytest.raises(SidParseError, match="has no spec"):
        JchPlayer(moved)


def test_a_wave_row_past_the_table_is_played_by_the_spec():
    model = _model([(synth.WAVE_NOTE, bytes([0xE0, 0x60]))])
    assert engine_of(make_player(model)) == "v20"


def test_the_pitch_tail_names_the_variable_at_each_byte_past_the_table():
    base = 0x1000
    wild = pitch_tail(base + 0x66D, base)
    assert len(wild) == 66 and wild[0] == WILD_KINDS.index("vibdelta") + 1 << 2
    tempo = 0x747 - 0x72D
    assert wild[tempo] == (WILD_KINDS.index("tempo") + 1) << 2
    vibperiod = (WILD_KINDS.index("vibperiod") + 1) << 2
    assert [wild[0x76C - 0x72D + v] for v in range(3)] == [
        vibperiod + v for v in range(3)
    ]
    assert pitch_tail(0x3000, base) == [0] * 66


@pytest.mark.parametrize("row", [0x60, 0xE0])
def test_a_wave_row_past_the_ninety_six_notes_plays_the_variable_there(row):
    """Rows 0 and 1 are where the voices' wave pointers are in the first calls: note 96, relative
    ($60) or absolute ($E0), is the word of the vibrato temporary and the low byte of voice 0's
    order pointer, as it is when the pitch table is followed by the variables."""
    model = _model([(synth.WAVE_NOTE, bytes([row, row]))])
    bound = spec_binding(model)

    def bind(view, entry):
        layout = bound.bind(view, entry)
        layout["wild"] = pitch_tail(layout["pitch"], layout["pitch"] + 192 - 0x72D)
        return layout

    player = SpecPlayer(
        bound.spec,
        model.image,
        model.load_addr,
        model.init_addr,
        model.play_addr,
        0,
        bind=bind,
    )
    player.play_frame()
    player.play_frame()
    order = player.state("voice[0].start") + player.state("voice[0].orderpos")
    want = player.state("vibdelta") | (order & 0xFF) << 8
    assert want == 256
    regs = player.snapshot()
    assert regs[14] | regs[15] << 8 == want + player.state("voice[2].detune")


def test_the_fixture_names_a_reason_the_binder_gives():
    assert REFUSED and {reason for _path, reason in REFUSED} <= set(SPEC_REFUSALS)
    assert len({path for path, _reason in REFUSED}) == len(REFUSED)


@pytest.mark.oracle
@pytest.mark.parametrize("relpath,reason", REFUSED)
def test_an_hvsc_tune_is_refused_for_its_reason(relpath, reason, hvsc):
    model = reader.parse(hvsc(relpath).read_bytes())
    assert spec_gate(model) == (None, reason)
    assert (
        type(make_player(model)) is EmuPlayer
    )  # pylint: disable=unidiomatic-typecheck
