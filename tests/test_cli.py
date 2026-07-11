"""CLI: JCH plugs into the generic ``pysidtracker`` CLI (info/reglog/wav/export).

pyjch ships no ``pyjch`` binary; it registers a ``jch`` :class:`SidFormat` on the
``pysidtracker.formats`` entry-point group. These tests drive the shared
``pysidtracker.maincli`` with that format.
"""

import json
import struct

import pytest

from pysidtracker import SidError, discover_formats, read_reglog
from pysidtracker import maincli

from pyjch.cli_plugin import FORMAT
from tests import _synth_v20 as synth
from tests.test_newplayer import _base_image
from tests.tunes import V0X_REFERENCES


def _main(argv):
    """Run the generic CLI with only the JCH format registered."""
    return maincli.run_cli(lambda: maincli.build_parser([FORMAT]), SidError, argv)


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


def test_entry_point_registered():
    """Installing pyjch surfaces the ``jch`` format to the generic CLI."""
    assert any(fmt.name == "jch" for fmt in discover_formats())


def test_info_family_model(capsys, tmp_path):
    path = _family_psid(tmp_path)
    assert _main(["info", str(path)]) == 0
    out = capsys.readouterr().out
    assert "format:   jch" in out
    assert "EmuPlayer" in out  # a family build plays byte-exact via emulation
    assert "instruments:" in out
    assert "orderlist @" in out


def test_reglog_family_emulated(capsys, tmp_path):
    # A recovered family version plays (byte-exact) via EmuPlayer, so the reglog
    # is produced rather than refused.
    path = _family_psid(tmp_path)
    out = tmp_path / "out.reglog"
    assert _main(["reglog", str(path), str(out), "--seconds", "1"]) == 0
    assert out.exists()
    assert "wrote" in capsys.readouterr().out


@pytest.mark.parametrize("relpath", V0X_REFERENCES)
def test_info(capsys, relpath, hvsc):
    assert _main(["info", str(hvsc(relpath))]) == 0
    out = capsys.readouterr().out
    assert "load:" in out
    assert "gate on/off:" in out


@pytest.mark.parametrize("relpath", V0X_REFERENCES)
def test_reglog(tmp_path, relpath, hvsc):
    dst = tmp_path / "tune.reglog"
    assert _main(["reglog", str(hvsc(relpath)), str(dst), "--seconds", "1"]) == 0
    assert read_reglog(dst)


def test_error_on_missing_file(capsys):
    assert _main(["info", "/nonexistent/tune.sid"]) == 1
    assert "error:" in capsys.readouterr().err


def test_export_json(capsys, tmp_path):
    out = tmp_path / "tune.json"
    assert _main(["export", str(_synth_sid(tmp_path)), str(out)]) == 0
    assert json.loads(out.read_text())["provenance"]["tier"] == "v20"
    assert "wrote" in capsys.readouterr().out


def test_export_text(tmp_path):
    out = tmp_path / "tune.txt"
    assert (
        _main(["export", str(_synth_sid(tmp_path)), str(out), "--format", "text"]) == 0
    )
    assert "tier: v20" in out.read_text()


def test_export_editor_prg(tmp_path):
    driver = tmp_path / "driver.prg"
    driver.write_bytes(bytes(0x2000))
    out = tmp_path / "tune.prg"
    assert (
        _main(
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
        == 0
    )
    prg = out.read_bytes()
    assert prg[0] | (prg[1] << 8) == 0x0F00


def test_export_editor_prg_default_driver_and_version(tmp_path):
    out = tmp_path / "tune.prg"
    assert (
        _main(
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
        == 0
    )
    img = out.read_bytes()[2:]
    assert bytes(img[0x0FEE - 0x0F00 : 0x0FEE - 0x0F00 + 5]) == b"22.4X"


def test_requires_subcommand():
    with pytest.raises(SystemExit):
        _main([])
