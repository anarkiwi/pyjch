"""CLI: info and reglog subcommands."""

import pytest

from pyjch import cli
from pyjch.reglog import read_reglog


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
