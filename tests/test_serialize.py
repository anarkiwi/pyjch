"""Round-trip and text-dump tests for the neutral song model serializer."""

import json

from pyjch import newplayer, reader
from pyjch.extract import extract
from pyjch.serialize import from_json, to_json, to_text

from tests import _synth_v20 as synth
from tests.test_newplayer import INIT, LOAD, PLAY, _base_image


def _tune():
    return extract(reader.parse(synth.build_psid()))


def _family_tune():
    model = newplayer.recover(
        LOAD, INIT, PLAY, "n", "a", "r", _base_image(), b"", "V14", 1
    )
    return extract(model)


def test_family_round_trip_and_text_notes():
    tune = _family_tune()
    assert from_json(to_json(tune)) == tune
    dump = to_text(tune)
    assert "tier: family" in dump
    assert "notes:" in dump


def test_json_round_trip_is_exact():
    tune = _tune()
    text = to_json(tune)
    assert from_json(text) == tune


def test_json_is_valid_and_enums_by_value():
    tune = _tune()
    data = json.loads(to_json(tune))
    assert data["provenance"]["tier"] == "v20"
    assert data["patterns"][0][0]["command"]["kind"] == "portamento"
    assert data["patterns"][3][0]["note"]["kind"] == "hold"


def test_text_dump_is_non_empty_and_stable():
    tune = _tune()
    dump = to_text(tune)
    assert dump == to_text(tune)
    assert "tier: v20" in dump
    assert "pattern 0:" in dump
    assert dump.endswith("\n")
