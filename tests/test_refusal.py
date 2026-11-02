"""The V20 tunes the spec refuses: the facts it refuses them by, and the HVSC tunes that are."""

import dataclasses
from pathlib import Path

import pytest

from pyjch import reader
from pyjch.errors import SidParseError
from pyjch.constants import (
    OVERLAP,
    PITCH_REACH,
    SONG_UNPARSED,
    SPEC_REFUSALS,
    VOICE_CELLS,
)
from pyjch.player import (
    JchPlayer,
    engine_of,
    make_player,
    spec_gate,
    v0x_refused,
    v20_bases,
)
from pyjch.refusal import _past, refusal
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


def test_a_song_that_does_not_parse_is_refused():
    model = _model([(synth.PATTERNS, bytes([0x30]) * 0x100)])
    assert spec_gate(model) == (None, SONG_UNPARSED)


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


@pytest.mark.parametrize(
    "row,reason", [(0xDF, "reference"), (0xE0, PITCH_REACH), (0x60, PITCH_REACH)]
)
def test_a_wave_row_past_the_ninety_six_notes_is_refused(row, reason):
    """Row 0 starts the first instrument and is where the voices' wave pointers are."""
    model = _model([(synth.WAVE_NOTE, bytes([row]))])
    assert spec_gate(model)[1] == reason
    assert engine_of(make_player(model)) == ("v20" if reason == "reference" else "emu")


@pytest.mark.parametrize(
    "rows,shift,past",
    [
        (1 << 60, 35, False),
        (1 << 60, 36, True),
        (1 << 100, 28, False),
        (1 << 95, 1, True),
        (1 << (128 + 95), 0, False),
        (1 << (128 + 96), 0, True),
    ],
)
def test_a_note_lands_past_the_table_by_its_sum_modulo_128(rows, shift, past):
    assert _past(rows, shift) is past


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
