"""The wave, pulse/filter and command tables behave as the grammar and docs say."""

import collections

import pytest

from pyjch import grammar, reader
from pyjch.cli_plugin import FORMAT
from pyjch.errors import SidParseError
from pyjch.extract import extract
from pyjch.player import JchPlayer

from tests import _synth_v20 as synth

BASE = 0x1000  # the code base of the synthetic tune
CALLS = 600
VOLUME_SLOT = 4  # the synthetic pattern's own volume command, replaced by a patch


def _model(patches):
    """The synthetic tune with ``(address, bytes)`` patches applied to its image."""
    data = bytearray(synth.build_psid())
    start = len(data) - synth.IMAGE_LEN
    for addr, blob in patches:
        at = start + addr - synth.LOAD
        data[at : at + len(blob)] = blob
    return reader.parse(bytes(data))


def _waveforms(note_col, ctrl_col):
    """The waveform bits written to voice 0's control register, per call."""
    player = JchPlayer(
        _model([(synth.WAVE_NOTE, note_col), (synth.WAVE_CTRL, ctrl_col)])
    )
    out = []
    for _ in range(CALLS):
        player.play_frame()
        out.append(player.memory[0xD404] & 0xF0)
    return out


ROWS = b"\x0c\x10"
PAD = b"\x00\x00\x00"


def test_a_jump_row_returns_to_the_row_its_control_byte_names():
    """Row 0 plays a waveform of its own; a jump to row 0 keeps playing it."""
    to_first = collections.Counter(
        _waveforms(ROWS + b"\x7f" + PAD, b"\x41\x21\x00" + PAD)
    )
    to_second = collections.Counter(
        _waveforms(ROWS + b"\x7f" + PAD, b"\x41\x21\x01" + PAD)
    )
    assert to_first[0x40] > 3 * to_second[0x40] > 0


def test_a_hold_row_stays_on_the_row_before_it():
    """``$7E`` plays exactly what a jump to the previous row plays."""
    held = _waveforms(ROWS + b"\x7e" + PAD, b"\x41\x21\x00" + PAD)
    jumped = _waveforms(ROWS + b"\x7f" + PAD, b"\x41\x21\x01" + PAD)
    assert held == jumped


def _cells(first, second, slot=3):
    """Tempo, volume, vibrato depth and portamento speed after command ``slot`` runs."""
    patches = [(synth.CMDPARAM + 2 * slot, bytes([first, second]))]
    if slot != VOLUME_SLOT:
        patches.append((synth.CMDPARAM + 2 * VOLUME_SLOT, b"\x70\x00"))
    player = JchPlayer(_model(patches))
    player.render_grid(CALLS)
    mem = player.memory
    return {
        "tempo": mem[BASE + 0x747],
        "volume": mem[BASE + 0x009],
        "vibrato": mem[BASE + 0x77E],
        "portamento": mem[BASE + 0x79E],
    }


UNTOUCHED = _cells(0x70, 0x09)  # a patch entry changes none of the four
KIND_CELL = {
    "tempo": "tempo",
    "volume": "volume",
    "vibrato": "vibrato",
    "portamento": "portamento",
}
FIRST_BYTES = [0x00, 0x2F, 0x30, 0x5F, 0x60, 0x6F, 0x70, 0x8F, 0x90, 0x9F, 0xA0]
FIRST_BYTES += [0xDF, 0xE0, 0xEF, 0xF0, 0xFF]


@pytest.mark.parametrize("first", FIRST_BYTES)
def test_the_kind_the_grammar_gives_a_command_is_the_one_the_player_runs(first):
    kind = grammar.GRAMMAR.parse_exact(
        bytes([first, 0x09]), grammar.COMMAND_TABLE
    ).entries[0][0]
    after = _cells(first, 0x09)
    changed = {name for name in after if after[name] != UNTOUCHED[name]}
    assert changed == ({KIND_CELL[kind]} if kind in KIND_CELL else set())
    if changed:
        assert after[KIND_CELL[kind]] == 0x09


def test_the_second_byte_is_the_parameter_each_kind_applies():
    assert _cells(0xF0, 0x0A)["volume"] == 0x0A
    assert _cells(0xE0, 0x0B)["tempo"] == 0x0B
    assert _cells(0x60, 0x3C)["vibrato"] == 0x0C  # the low nibble is the depth
    assert _cells(0x10, 0x07)["portamento"] == 0x07


def test_extract_rejects_a_wave_program_that_never_ends():
    model = _model([(synth.WAVE_NOTE, bytes([0x0C]) * 0x40)])
    with pytest.raises(SidParseError, match="wave program"):
        extract(model)


def test_the_listing_of_a_v20_tune_includes_its_tables():
    model = _model([])
    names = [line[:-1] for line in FORMAT.listing(model) if not line.startswith(" ")]
    assert any(name.startswith("wave program") for name in names)
    assert {"pulse table", "filter table", "command table"} <= set(names)
    grammar.GRAMMAR.check(FORMAT.streams(model), SidParseError)
