"""Register log surface: the mandatory validator interface."""

import io

import pytest
from pysidtracker import SidParseError as SharedSidParseError

from pyjch import constants, reader, reglog
from pyjch.errors import JCHError


def test_iter_register_writes_clocking(tune_path):
    """An init baseline burst at clock 0, then play calls one frame later.

    The init->play gap must exceed one frame so an oracle framer anchors
    frame 0 to the first play call (never to a later run of silent frames).
    """
    song = reader.read(tune_path)
    writes = list(reglog.iter_register_writes(song, max_frames=4))
    assert writes
    assert all(isinstance(w, reglog.RegWrite) for w in writes)
    # The init burst writes all 25 registers at clock 0..spacing*24.
    init = [w for w in writes if w.clock < constants.PAL_CYCLES_PER_FRAME]
    assert len(init) == constants.SID_REGISTERS
    assert init[0].clock == 0
    assert init[1].clock == reglog.DEFAULT_WRITE_SPACING
    # The first play call is one full frame later (a > one-frame gap).
    first_play = min(
        w.clock for w in writes if w.clock >= constants.PAL_CYCLES_PER_FRAME
    )
    assert first_play == constants.PAL_CYCLES_PER_FRAME
    gap = first_play - init[-1].clock
    assert gap > 10000


def test_write_spacing_guard(tune_path):
    """A write_spacing that overflows the frame is rejected."""
    song = reader.read(tune_path)
    with pytest.raises(JCHError):
        list(reglog.iter_register_writes(song, max_frames=1, write_spacing=10000))


def test_round_trip_text(tune_path):
    """A register log serializes and parses back identically."""
    song = reader.read(tune_path)
    writes = list(reglog.iter_register_writes(song, max_frames=8))
    buf = io.StringIO()
    reglog.write_reglog(writes, buf)
    buf.seek(0)
    parsed = reglog.read_reglog(buf)
    assert parsed == writes


def test_write_and_read_path(tmp_path, tune_path):
    """write_reglog/read_reglog round-trip through a file path."""
    song = reader.read(tune_path)
    writes = list(reglog.iter_register_writes(song, max_frames=8))
    dst = tmp_path / "tune.reglog"
    reglog.write_reglog(writes, dst)
    assert reglog.read_reglog(dst) == writes


def test_read_reglog_skips_comments_and_blanks():
    text = "# header\n\n0 4 136  # gate\n19656 0 12\n"
    writes = reglog.read_reglog(io.StringIO(text))
    assert writes == [reglog.RegWrite(0, 4, 136), reglog.RegWrite(19656, 0, 12)]


def test_read_reglog_bad_line():
    with pytest.raises(SharedSidParseError):
        reglog.read_reglog(io.StringIO("1 2\n"))


def test_read_reglog_non_integer():
    with pytest.raises(SharedSidParseError):
        reglog.read_reglog(io.StringIO("a b c\n"))


def test_read_reglog_rejects_unknown_source():
    with pytest.raises(TypeError):
        reglog.read_reglog(42)
