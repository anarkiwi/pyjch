"""Register log surface: the mandatory validator interface."""

import io

import pytest

from pyjch import constants, reader, reglog
from pyjch.errors import JCHError


def test_iter_register_writes_clocking(tune_path):
    """Clocks advance by cycles_per_frame across frames, write_spacing within."""
    song = reader.read(tune_path)
    writes = list(reglog.iter_register_writes(song, max_frames=4))
    assert writes
    assert all(isinstance(w, reglog.RegWrite) for w in writes)
    # The first frame writes all 25 registers at frame clock 0.
    frame0 = [w for w in writes if w.clock < constants.PAL_CYCLES_PER_FRAME]
    assert len(frame0) == constants.SID_REGISTERS
    assert frame0[0].clock == 0
    assert frame0[1].clock == reglog.DEFAULT_WRITE_SPACING


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
    with pytest.raises(JCHError):
        reglog.read_reglog(io.StringIO("1 2\n"))


def test_read_reglog_non_integer():
    with pytest.raises(JCHError):
        reglog.read_reglog(io.StringIO("a b c\n"))


def test_read_reglog_rejects_unknown_source():
    with pytest.raises(TypeError):
        reglog.read_reglog(42)
