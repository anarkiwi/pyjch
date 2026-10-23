"""The ``scripts/oracle_hvsc.py`` survey renderer and its render contract."""

import json
import re

import numpy as np
import oracle_hvsc
import pytest

import pysidtracker
from pysidtracker.cadence import cadence_info
from pysidtracker.sidid import sidid_signatures

from pyjch import JCHParseError, JchPlayer, reader

from tests import _synth_v20 as synth
from tests.tunes import V0X_REFERENCES


def test_render_matches_player():
    data = synth.build_psid()
    grid, info = oracle_hvsc.render(data, 1, 120)
    assert grid.shape == (120, 25)
    assert grid.dtype == np.int16
    np.testing.assert_array_equal(grid, JchPlayer(reader.parse(data)).render_grid(120))
    assert json.loads(json.dumps(info)) == info
    assert info == {
        "engine": "v20",
        "version": "JCH_NewPlayer",
        "rsid": False,
        "trigger": None,
        "speed": 1,
        "dynamic": False,
    }


def test_render_v0x_reports_v0x(hvsc):
    grid, info = oracle_hvsc.render(hvsc(V0X_REFERENCES[0]).read_bytes(), 1, 30)
    assert grid.shape == (30, 25)
    assert (info["engine"], info["version"]) == ("v0x", "V0x")


def test_render_selects_one_based_subtune(monkeypatch):
    seen = []

    def player(model, subtune):
        seen.append(("player", subtune))
        return JchPlayer(model)

    def cadence(data, song):
        seen.append(("cadence", song))
        return cadence_info(data, song)

    monkeypatch.setattr(
        oracle_hvsc, "parse", lambda _: reader.parse(synth.build_psid())
    )
    monkeypatch.setattr(oracle_hvsc, "make_player", player)
    monkeypatch.setattr(oracle_hvsc, "cadence_info", cadence)
    oracle_hvsc.render(synth.build_psid(songs=3), 3, 10)
    assert sorted(seen) == [("cadence", 3), ("player", 2)]


@pytest.mark.parametrize("data", [b"PSID", synth.build_psid()[:0x7C] + b"\x00" * 0x800])
def test_non_jch_raises_sid_parse_error(data):
    with pytest.raises(pysidtracker.SidParseError) as exc:
        oracle_hvsc.render(data, 1, 10)
    assert isinstance(exc.value, JCHParseError)


def test_player_regex_selects_jch_newplayer_signatures():
    names = [name for name, _ in sidid_signatures()]
    hits = [n for n in names if re.search(oracle_hvsc.PLAYER, n, re.I)]
    assert hits[0] == "JCH_NewPlayer"
    assert set(hits[1:]) == {
        f"(JCH_NewPlayer_V{v})"
        for v in [*range(1, 16), 17, 18, 19, 20, "0x"]
        if v != 16
    }
