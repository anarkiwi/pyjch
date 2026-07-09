"""CLI: info and reglog subcommands."""

import struct

import pytest

from pyjch import cli
from pyjch.reglog import read_reglog
from tests.test_newplayer import _base_image


def _family_psid(tmp_path):
    """A PSID (init=$1000, play=$1003) wrapping a synthetic family image."""
    image = _base_image()
    header = struct.pack(
        ">4sHHHHHHHI32s32s32sHBBBB",
        b"PSID",
        2,
        0x7C,
        0,  # loadAddress 0 -> real load is first 2 image bytes
        0x1000,
        0x1003,
        1,
        1,
        0,
        b"synthetic",
        b"pyjch",
        b"2026",
        0,
        0,
        0,
        0,
        0,
    )
    data = header + struct.pack("<H", 0x1000) + image
    dst = tmp_path / "family.sid"
    dst.write_bytes(data)
    return dst


def test_info_family_model(capsys, tmp_path):
    path = _family_psid(tmp_path)
    rc = cli.main(["info", str(path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "model recovered" in out
    assert "instruments:" in out
    assert "orderlist @" in out


def test_reglog_family_unsupported(capsys, tmp_path):
    path = _family_psid(tmp_path)
    rc = cli.main(["reglog", str(path), str(tmp_path / "out.reglog")])
    assert rc == 1
    assert "byte-exact playback is not supported" in capsys.readouterr().err


def test_info(capsys, tune_path):
    rc = cli.main(["info", str(tune_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "load:" in out
    assert "gate on/off:" in out


def test_reglog(tmp_path, tune_path):
    dst = tmp_path / "tune.reglog"
    rc = cli.main(["reglog", str(tune_path), str(dst), "--seconds", "1"])
    assert rc == 0
    writes = read_reglog(dst)
    assert writes


def test_error_on_missing_file(capsys):
    rc = cli.main(["info", "/nonexistent/tune.sid"])
    assert rc == 1
    assert "error:" in capsys.readouterr().err


def test_requires_subcommand():
    with pytest.raises(SystemExit):
        cli.main([])
