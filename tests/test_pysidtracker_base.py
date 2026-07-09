"""pysidtracker base-library integration: error hierarchy and parser adapter."""

import pysidtracker

from pyjch import reader
from pyjch.errors import JCHError, SidParseError
from pyjch.reader import JchSidParser


def test_base_error_subclasses_sid_error():
    """pyjch's error hierarchy re-parents onto pysidtracker.SidError."""
    assert issubclass(JCHError, pysidtracker.SidError)
    assert issubclass(SidParseError, JCHError)
    assert issubclass(SidParseError, pysidtracker.SidError)


def test_parser_read_matches_module_parse(tune_path):
    """JchSidParser().read(path) is value-identical to reader.parse(bytes)."""
    data = tune_path.read_bytes()
    from_parser = JchSidParser().read(tune_path)
    from_bytes = reader.parse(data)
    assert from_parser.load_addr == from_bytes.load_addr
    assert from_parser.init_addr == from_bytes.init_addr
    assert from_parser.play_addr == from_bytes.play_addr
    assert from_parser.image == from_bytes.image
    assert from_parser.container_header == from_bytes.container_header
    assert from_parser.freq_lo == from_bytes.freq_lo
    assert from_parser.freq_hi == from_bytes.freq_hi
    assert from_parser.subptr_lo == from_bytes.subptr_lo
    assert from_parser.subptr_hi == from_bytes.subptr_hi


def test_parser_detect_falls_back_without_recognizer(tune_path):
    """No static recognizer -> detect() yields UNKNOWN (no false DIRECT)."""
    detection = JchSidParser().detect(tune_path, init=False)
    assert detection.kind is pysidtracker.PlayroutineKind.UNKNOWN
