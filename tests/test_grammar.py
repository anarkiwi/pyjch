"""The song-stream grammar: what the player reads, and what it rejects."""

import pytest

from pyjch import grammar, newplayer
from pyjch.extract import extract
from pyjch.cli_plugin import FORMAT
from pyjch.errors import SidParseError
from tests.test_newplayer import INIT, LOAD, PAT0, PLAY, _base_image, _put


def _model(image=None, version="V14"):
    return newplayer.recover(
        LOAD, INIT, PLAY, "n", "a", "r", image or _base_image(), b"", version, 1
    )


def _parse(data, rule):
    return grammar.GRAMMAR.parse_prefix(bytes(data), rule)


def test_orderlist_last_transpose_before_a_pattern_wins():
    parsed = _parse([0x81, 0x9C, 0x03, 0x04, 0xFE], grammar.ORDERLIST)
    assert parsed.model.steps == ((3, 0x9C), (4, 0))
    assert not parsed.model.loops
    assert parsed.size == 5


def test_orderlist_ff_loops_and_fe_stops():
    assert _parse([0x00, 0xFF], grammar.ORDERLIST).model.loops
    assert not _parse([0x00, 0xFE], grammar.ORDERLIST).model.loops


def test_bytes_after_the_terminator_are_not_part_of_the_stream():
    parsed = _parse([0x02, 0xFE, 0x05, 0x06], grammar.ORDERLIST)
    assert parsed.model.raw == b"\x02\xfe"
    assert parsed.size == 2


def test_unterminated_orderlist_is_a_parse_error():
    with pytest.raises(SidParseError, match="no terminator"):
        _parse([0x00, 0x01, 0x02], grammar.ORDERLIST)


def test_pattern_ends_at_7f_and_ff_is_a_command():
    parsed = _parse([0xA2, 0x24, 0xFF, 0x7E, 0x7F, 0x30], grammar.PATTERN)
    assert parsed.model.events == b"\xa2\x24\xff\x7e"
    assert parsed.size == 5


def test_interleaved_wave_patterns_end_at_ff_and_carry_7f_as_data():
    parsed = _parse([0x83, 0x7F, 0x18, 0xFF, 0x7F], grammar.PATTERN_V1)
    assert parsed.model.events == b"\x83\x7f\x18"
    assert parsed.size == 4


def test_records_are_fixed_width():
    assert _parse(range(8), grammar.INSTRUMENT).size == 8
    assert _parse(range(4), grammar.STEP).size == 4
    with pytest.raises(SidParseError):
        _parse(range(7), grammar.INSTRUMENT)


def test_extract_rejects_a_pattern_that_never_terminates():
    image = bytearray(_base_image())
    _put(image, PAT0, bytes([0x30] * (len(image) - (PAT0 - LOAD))))
    with pytest.raises(SidParseError, match="pattern 0"):
        extract(_model(bytes(image)))


def test_pattern_rule_follows_the_wave_layout():
    assert grammar.pattern_rule(_model()) == grammar.PATTERN
    interleaved = _model()
    interleaved.wave_stream = 0x1234
    assert grammar.pattern_rule(interleaved) == grammar.PATTERN_V1


def test_format_lists_the_streams_it_reads():
    lines = list(FORMAT.listing(_model()))
    assert "order list 0.0:" in lines
    assert "  orderlist TRANSPOSE=$8c PATTERN=$00 PATTERN=$01 LOOP=$ff" in lines
    assert "  pattern COMMAND=$a0 NOTE=$24 END=$7f" in lines


def test_every_listed_stream_reads_back_as_itself():
    model = _model()
    grammar.GRAMMAR.check(FORMAT.streams(model), SidParseError)
