"""The canonical text: equal exactly for tunes that play the same."""

from pyjch import reader
from pyjch.cli_plugin import FORMAT

from tests import _synth_v20 as synth


def _text(data):
    return FORMAT.canonical_text(reader.parse(bytes(data)))


def _at(data, addr):
    return len(data) - synth.IMAGE_LEN + addr - synth.LOAD


def _swap_patterns_zero_and_one(data):
    """The same song with patterns 0 and 1 trading places in memory and in voice 0."""
    first = _at(data, synth.PATTERNS)
    second = first + 0x10
    data[first : first + 0x10], data[second : second + 0x10] = (
        data[second : second + 0x10],
        data[first : first + 0x10],
    )
    order = _at(data, synth.ORDER[0])
    data[order], data[order + 1] = data[order + 1], data[order]


def test_a_tune_is_canonically_equal_to_itself():
    assert _text(synth.build_psid()) == _text(synth.build_psid())


def test_renumbering_the_patterns_does_not_change_the_canonical_text():
    data = bytearray(synth.build_psid())
    _swap_patterns_zero_and_one(data)
    assert bytes(data) != synth.build_psid()
    assert _text(data) == _text(synth.build_psid())


def test_changing_a_played_note_changes_the_canonical_text():
    data = bytearray(synth.build_psid())
    data[_at(data, synth.PATTERNS)] ^= 0x01
    assert _text(data) != _text(synth.build_psid())
