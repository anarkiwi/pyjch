"""The detected :class:`~pysidtracker.Variant` of a parsed JCH NewPlayer model."""

from pyjch import Song, detect_variant, newplayer, reader
from pyjch.cli_plugin import FORMAT
from pyjch.extract import extract
from pyjch.variant import facet_sites

from tests import _synth_v20 as synth
from tests.test_newplayer import INIT, LOAD, PLAY, _base_image


def _family():
    return newplayer.recover(
        LOAD, INIT, PLAY, "n", "a", "r", _base_image(), b"", "T", 1
    )


def test_v20_variant_facets():
    variant = detect_variant(reader.parse(synth.build_psid()))
    assert variant["model"] == "family"
    assert variant["engine"] == "v20"
    assert variant["v20_gate"] == "reference"
    assert variant["hard_restart"] == 0x09
    assert variant["entry_points"] == "jmp-vectors"
    assert variant["v0x_layout"] == "none"


def test_hard_restart_is_detected_at_its_site():
    model = reader.parse(synth.build_psid(hard_restart=0x11))
    variant = detect_variant(model)
    assert variant["hard_restart"] == 0x11
    assert variant.source("hard_restart").kind == "detected"
    assert facet_sites(model)[variant.source("hard_restart").addr] == "hard_restart"


def test_family_variant_reports_the_rejecting_gate_and_defaults():
    variant = detect_variant(_family())
    assert variant["engine"] == "emu"
    assert variant["v20_gate"] == "no-pitch-or-wave"
    assert variant["hard_restart"] == 0x09
    assert variant.source("hard_restart").kind == "default"
    assert variant["pitch_form"] == "none"
    assert variant["wave_layout"] == "none"
    assert variant["entry_points"] == "jmp-vectors"
    assert variant.source("entry_points").kind == "default"  # headerless container


def test_v0x_variant():
    variant = detect_variant(Song())
    assert variant["model"] == "v0x"
    assert variant["version"] == "V0x"
    assert variant["engine"] == "v0x"
    assert variant["v20_gate"] == "n/a"
    assert variant["hard_restart"] is None
    assert variant["v0x_layout"] == "fixed"


def test_as_info_carries_a_source_per_facet():
    info = detect_variant(reader.parse(synth.build_psid())).as_info()
    assert info["engine"] == "v20"
    assert all(f"{name}.src" in info for name in ("model", "version", "engine"))


def test_format_exposes_the_variant_hooks():
    model = reader.parse(synth.build_psid())
    assert FORMAT.variant_info(model)["engine"] == "v20"
    assert FORMAT.sites(model) == facet_sites(model)


def test_tune_provenance_records_the_variant():
    tune = extract(reader.parse(synth.build_psid()))
    assert tune.provenance.variant["engine"] == "v20"
    assert tune.provenance.variant["v20_gate"] == "reference"
    family = extract(_family())
    assert family.provenance.variant["engine"] == "emu"
    assert family.provenance.tier == "family"
