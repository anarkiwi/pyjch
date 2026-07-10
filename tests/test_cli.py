"""CLI: info and reglog subcommands."""

import json
import struct

import pytest

from pyjch import cli
from pyjch.reglog import read_reglog
from tests import _synth_v20 as synth
from tests.test_newplayer import _base_image


def _synth_sid(tmp_path):
    dst = tmp_path / "synth.sid"
    dst.write_bytes(synth.build_psid())
    return dst


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


def test_export_json(capsys, tmp_path):
    out = tmp_path / "tune.json"
    rc = cli.main(["export", str(_synth_sid(tmp_path)), str(out)])
    assert rc == 0
    assert json.loads(out.read_text())["provenance"]["tier"] == "v20"
    assert "wrote" in capsys.readouterr().out


def test_export_text(tmp_path):
    out = tmp_path / "tune.txt"
    rc = cli.main(["export", str(_synth_sid(tmp_path)), str(out), "--format", "text"])
    assert rc == 0
    assert "tier: v20" in out.read_text()


def test_export_editor_prg(tmp_path):
    driver = tmp_path / "driver.prg"
    driver.write_bytes(bytes(0x2000))
    out = tmp_path / "tune.prg"
    rc = cli.main(
        [
            "export",
            str(_synth_sid(tmp_path)),
            str(out),
            "--format",
            "editor-prg",
            "--driver",
            str(driver),
        ]
    )
    assert rc == 0
    prg = out.read_bytes()
    assert prg[0] | (prg[1] << 8) == 0x0F00


def test_export_editor_prg_default_driver_and_version(tmp_path):
    out = tmp_path / "tune.prg"
    rc = cli.main(
        [
            "export",
            str(_synth_sid(tmp_path)),
            str(out),
            "--format",
            "editor-prg",
            "--np-version",
            "22",
        ]
    )
    assert rc == 0
    prg = out.read_bytes()
    assert prg[0] | (prg[1] << 8) == 0x0F00
    img = prg[2:]
    assert bytes(img[0x0FEE - 0x0F00 : 0x0FEE - 0x0F00 + 5]) == b"22.4X"


def test_requires_subcommand():
    with pytest.raises(SystemExit):
        cli.main([])
