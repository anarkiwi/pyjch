"""Real-HVSC corpus test for the JCH NewPlayer reader.

pyjch reads two tiers of JCH NewPlayer tune:

* the canonical **``JCH_NewPlayer_V0x``** layout, which the byte-exact player
  replays (validated against the ``preframr-sidtrace`` oracle) -- ``parse``
  returns a :class:`~pyjch.model.Song`;
* the later **wavetable family** (V1/V2/V6/V8/V9/V10/V11/V13/V14/V15/V17/V18/
  V20), whose *song DATA* the version-aware reader recovers directly from the
  loaded image -- ``parse`` returns a :class:`~pyjch.newplayer.NewPlayerModel`.
  Playback of these versions is not byte-exact-verified; the contract tested
  here is that the recovered **model is coherent** (table bases in range, order
  lists terminate through in-range pattern pointers, an instrument record is
  present), never garbage.

A handful of genuinely different players (V3/V4/V5/V7/V12/V19, Glover's and
Dane's forks, the DigiPlayer) use a different data layout the reader cannot
recover; they must be cleanly **rejected**, not mis-parsed.

Tunes are HVSC copyright works, never committed.  Each tune is resolved from a
local HVSC tree (``$HVSC`` or ``$JCH_LOCAL_HVSC``) or the gitignored fetch
cache; set ``PYJCH_FETCH_CORPUS=1`` to allow on-demand download.  A tune that
cannot be resolved is skipped, so the suite passes offline and runs for real
when ``$HVSC`` points at a local tree.  See ``docs/versions.md`` for the full
per-version HVSC census.
"""

import os
import sys
from pathlib import Path

import pytest

from pyjch import reader
from pyjch.editor import NP25_PROFILE, _require_tables, np_profile, write_editor_prg
from pyjch.errors import SidParseError
from pyjch.extract import extract
from pyjch.model import Song
from pyjch.newplayer import NewPlayerModel
from pyjch.reader import JchSidParser
from pysidtracker import SidImage
from pysidtracker.detect import PlayroutineKind

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import fetch_tunes  # noqa: E402  (after sys.path tweak)

# The V0x layout the byte-exact player replays (proven against the oracle in
# tests/fixtures/*.grid.txt).
SUPPORTED = {
    "Flexible/Scorpio (V0x)": "MUSICIANS/S/Scorpio/Flexible.sid",
    "Simple_Tune/JCH (V0x)": "MUSICIANS/J/JCH/Simple_Tune.sid",
}

# One deterministic representative per wavetable-family version whose song
# model the reader recovers.  ``parse`` returns a coherent NewPlayerModel.
MODEL_RECOVERED = {
    "V1": "MUSICIANS/J/JCH/Beatbassie.sid",
    "V2": "MUSICIANS/J/JCH/Caverns.sid",
    "V6": "MUSICIANS/J/JCH/Acid_1988.sid",
    "V8": "MUSICIANS/D/DRAX/King_Tut.sid",
    "V9": "MUSICIANS/D/DRAX/Acid.sid",
    "V10": "MUSICIANS/D/DRAX/Ballmania_1st_version.sid",
    "V11": "MUSICIANS/B/Bjerregaard_Johannes/USA_Tune.sid",
    "V13": "MUSICIANS/A/Abaddon/Apina.sid",
    "V14": "DEMOS/A-F/Fjellaporna.sid",
    "V15": "MUSICIANS/A/Ahz_The_Demon/Lunardive.sid",
    "V17": "DEMOS/0-9/1st_Chaff.sid",
    "V18": "MUSICIANS/A/Avalon/Bambino.sid",
    "V20": "DEMOS/0-9/7D_Funkt.sid",
}

# Genuinely different players: a different data layout (or an entirely
# different fork) the reader cannot recover, so it must reject them.
REJECTED = {
    "V3": "MUSICIANS/J/JCH/2cVee.sid",
    "V4": "MUSICIANS/J/JCH/Diflexing.sid",
    "V5": "DEMOS/UNKNOWN/Falcon_Intro_03_v1.sid",
    "V7": "MUSICIANS/J/JCH/Lonewolf.sid",
    "V12": "MUSICIANS/D/DRAX/Exorcist_game.sid",
    "V19": "DEMOS/G-L/I2_Bass_Loop.sid",
    "Glover_NewPlayer_V21": "DEMOS/M-R/Mendelssohn.sid",
    "Dane_NewPlayer": "MUSICIANS/M/Mitch_and_Dane/Dane/Au_Revoir.sid",
    "JCH_DigiPlayer": "MUSICIANS/J/JCH/Easy_Does_It.sid",
}

# Sidid-tagged V0x tunes that share the V0x init signature (so recognize()
# truthfully matches) but drive gate-on waveforms from an instrument table --
# neither the V0x player nor the family reader models them, so parse rejects.
REJECTED_V0X_VARIANT = {
    "V0x-table-variant/Problems": "MUSICIANS/J/JCH/Problems.sid",
    "V0x-table-variant/Imagination": "MUSICIANS/J/JCH/Imagination.sid",
}

# Directories dense with JCH NewPlayer tunes, walked by the bulk no-garbage
# test.  Deterministic (fixed dirs), bounded, and skipped when absent.
BULK_DIRS = (
    "MUSICIANS/D/DRAX",
    "MUSICIANS/L/Laxity",
    "MUSICIANS/J/JCH",
)


def _resolve(relpath):
    """A usable path to ``relpath``, or ``None`` if it cannot be obtained."""
    for env in ("HVSC", "JCH_LOCAL_HVSC"):
        base = os.environ.get(env)
        if base:
            cand = Path(base) / relpath
            if cand.exists():
                return cand
    dest = fetch_tunes.CACHE / relpath
    if dest.exists():
        return dest
    if os.environ.get("PYJCH_FETCH_CORPUS"):
        try:
            return fetch_tunes.fetch(relpath)
        except Exception:  # pylint: disable=broad-except  # offline -> skip
            return None
    return None


def _load(relpath):
    path = _resolve(relpath)
    if path is None:
        pytest.skip(f"{relpath} unavailable (no local HVSC, not cached)")
    return Path(path).read_bytes()


def _assert_coherent_model(model, load=0x1000):
    """A recovered NewPlayerModel describes a coherent song, not garbage."""
    end = load + len(model.image)
    assert isinstance(model, NewPlayerModel)
    assert model.load_addr == load
    for base in (
        model.subtune_table,
        model.patternptr_lo,
        model.patternptr_hi,
        model.instruments,
    ):
        assert load <= base < end
    # Subtune 0: three in-range order-list pointers and a tempo byte.
    ptrs = [model.orderlist_ptr(0, v) for v in range(3)]
    assert all(p is not None and load <= p < end for p in ptrs)
    assert model.subtune_tempo(0) is not None
    # Order lists walk to a terminator through in-range pattern pointers.
    total = 0
    for voice in range(3):
        indices = model.iter_orderlist(0, voice)
        for idx in indices:
            assert load <= model.pattern_ptr(idx) < end
        total += len(indices)
    assert total > 0
    # An instrument record is present.
    rec = model.instrument(0)
    assert rec is not None and len(rec) == 8


@pytest.mark.parametrize("relpath", SUPPORTED.values(), ids=list(SUPPORTED))
def test_supported_version_parses(relpath):
    """Every canonical V0x tune parses to a Song, recognises, detects DIRECT."""
    data = _load(relpath)
    song = reader.parse(data)
    assert isinstance(song, Song)
    assert song.load_addr == 0x1000
    assert 0 <= song.init_ad <= 0xFF
    assert 0 <= song.init_sr <= 0xFF
    assert song.load_addr <= song.subptr_lo
    assert song.load_addr <= song.subptr_hi
    assert reader.read(data).image == song.image

    parser = JchSidParser()
    assert parser.recognize(SidImage.from_bytes(data)) is not None
    assert parser.detect(data, init=False).kind is PlayroutineKind.DIRECT


@pytest.mark.parametrize("relpath", MODEL_RECOVERED.values(), ids=list(MODEL_RECOVERED))
def test_family_model_recovered(relpath):
    """A family representative parses to a coherent NewPlayerModel and recognises."""
    data = _load(relpath)
    model = reader.parse(data)
    _assert_coherent_model(model)
    assert reader.read(data).subtune_table == model.subtune_table

    parser = JchSidParser()
    assert parser.recognize(SidImage.from_bytes(data)) is not None
    assert parser.detect(data, init=False).kind is PlayroutineKind.DIRECT


@pytest.mark.parametrize("relpath", MODEL_RECOVERED.values(), ids=list(MODEL_RECOVERED))
def test_family_extracts_to_coherent_tune(relpath):
    """Every family representative extracts to a bounded, coherent Tune."""
    model = reader.parse(_load(relpath))
    tune = extract(model)
    assert tune.provenance.tier in ("v20", "family")
    assert tune.subtunes and all(len(s.order_lists) == 3 for s in tune.subtunes)
    # capacities respected (never garbage-unbounded)
    assert len(tune.subtunes) <= 31
    assert len(tune.patterns) <= 114
    assert 1 <= len(tune.instruments) <= 32
    # every order-list entry references a decoded pattern
    for sub in tune.subtunes:
        for order in sub.order_lists:
            for entry in order.entries:
                assert 0 <= entry.pattern < len(tune.patterns)
    if tune.provenance.tier == "family":
        assert tune.provenance.notes


@pytest.mark.parametrize("relpath", REJECTED.values(), ids=list(REJECTED))
def test_other_version_rejected(relpath):
    """A representative of every unrecoverable version is cleanly rejected."""
    data = _load(relpath)
    with pytest.raises(SidParseError):
        reader.parse(data)
    assert JchSidParser().recognize(SidImage.from_bytes(data)) is None


@pytest.mark.parametrize(
    "relpath", REJECTED_V0X_VARIANT.values(), ids=list(REJECTED_V0X_VARIANT)
)
def test_v0x_table_variant_rejected(relpath):
    """A V0x-family tune with a table-driven gate routine rejects, not garbage.

    recognize() truthfully matches the shared V0x init signature, but parse
    rejects the unsupported layout instead of emitting garbage.
    """
    data = _load(relpath)
    assert JchSidParser().recognize(SidImage.from_bytes(data)) is not None
    with pytest.raises(SidParseError):
        reader.parse(data)


# Per-version family table-discovery coverage, derived from the real HVSC run.
# For each MODEL_RECOVERED representative: whether the generalized idioms recover
# the wave note/ctrl columns and pitch table, whether the editor table gate
# (_require_tables) passes, and whether a *full* editor .prg export succeeds.
# ``gate`` implies note+ctrl+pitch discovered; ``export`` can still be blocked
# by an editor format capacity limit (e.g. a pattern exceeding 96 rows) that is
# independent of table discovery -- so ``gate and not export`` is honest.
FAMILY_COVERAGE = {
    "V1": {"note": False, "ctrl": False, "pitch": True, "gate": False, "export": False},
    "V2": {"note": False, "ctrl": False, "pitch": True, "gate": False, "export": False},
    "V6": {"note": True, "ctrl": True, "pitch": True, "gate": True, "export": True},
    "V8": {"note": True, "ctrl": True, "pitch": True, "gate": True, "export": True},
    "V9": {"note": True, "ctrl": True, "pitch": True, "gate": True, "export": True},
    "V10": {"note": True, "ctrl": True, "pitch": True, "gate": True, "export": True},
    "V11": {"note": True, "ctrl": True, "pitch": True, "gate": True, "export": True},
    "V13": {"note": True, "ctrl": True, "pitch": True, "gate": True, "export": True},
    "V14": {"note": True, "ctrl": True, "pitch": True, "gate": True, "export": False},
    "V15": {"note": True, "ctrl": True, "pitch": True, "gate": True, "export": False},
    "V17": {"note": True, "ctrl": True, "pitch": False, "gate": False, "export": False},
    "V18": {"note": True, "ctrl": True, "pitch": True, "gate": True, "export": True},
    "V20": {"note": True, "ctrl": True, "pitch": True, "gate": True, "export": True},
}


@pytest.mark.parametrize("version", MODEL_RECOVERED, ids=list(MODEL_RECOVERED))
def test_family_table_discovery_matrix(version):
    """Each representative reaches exactly its recorded table-discovery tier.

    Asserts the generalized wave-column / pitch idioms recover the tables the
    coverage map records, that the editor table gate passes for the full-table
    versions, and that a full ``$0F00`` editor ``.prg`` (with the wave/pitch
    tables laid) is emitted for those whose data fits the editor format.
    """
    cov = FAMILY_COVERAGE[version]
    model = reader.parse(_load(MODEL_RECOVERED[version]))
    tune = extract(model)
    wt = tune.wavetable
    assert bool(wt and wt.note_col) is cov["note"]
    assert bool(wt and wt.ctrl_col) is cov["ctrl"]
    assert bool(tune.pitch_table) is cov["pitch"]
    if cov["gate"]:
        # Discovered wave columns and pitch resolve to in-range, coherent bases.
        load, end = tune.load_addr, tune.load_addr + len(model.image)
        keys = {
            "family": ("wave_note_col", "wave_ctrl_col", "pitch_table"),
            "v20": ("wave_note", "wave_ctrl", "pitch"),
        }[tune.provenance.tier]
        for key in keys:
            base = tune.provenance.bases.get(key)
            assert base is not None and load <= base < end
        assert len(wt.note_col) <= 256 and len(wt.ctrl_col) <= 256
        _require_tables(tune, NP25_PROFILE)  # must not raise
    else:
        with pytest.raises(SidParseError):
            _require_tables(tune, NP25_PROFILE)
    if cov["export"]:
        prg = write_editor_prg(tune, profile=np_profile(25))
        assert prg[:2] == bytes([0x00, 0x0F])  # load at $0F00
        image = prg[2:]
        base = NP25_PROFILE.base_wave_ctrl - NP25_PROFILE.load_addr
        assert list(image[base : base + len(wt.ctrl_col)]) == wt.ctrl_col
    elif cov["gate"]:
        # Tables fully discovered but an editor format capacity limit blocks it.
        with pytest.raises(SidParseError):
            write_editor_prg(tune, profile=np_profile(25))


def test_bulk_family_full_discovery_count():
    """Across JCH-dense dirs, many family tunes now reach full table discovery.

    A recovered NewPlayerModel that discovers both wave columns and the pitch
    table must pass the editor table gate; asserts a healthy count reach it, so
    the generalized idioms are proven against the real corpus, not just reps.
    """
    base = _hvsc_root()
    if base is None:
        pytest.skip("no local HVSC tree for the bulk corpus walk")
    full = 0
    checked = 0
    for rel in BULK_DIRS:
        root = base / rel
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.sid")):
            checked += 1
            try:
                model = reader.parse(path.read_bytes())
            except SidParseError:
                continue
            if not isinstance(model, NewPlayerModel):
                continue
            tune = extract(model)
            wt = tune.wavetable
            if wt and wt.note_col and wt.ctrl_col and tune.pitch_table:
                _require_tables(tune, NP25_PROFILE)  # must not raise
                full += 1
    if checked == 0:
        pytest.skip("bulk dirs present but empty")
    assert full >= 300, f"expected many full-table discoveries, got {full}"


def _hvsc_root():
    for env in ("HVSC", "JCH_LOCAL_HVSC"):
        cand = os.environ.get(env)
        if cand and Path(cand).is_dir():
            return Path(cand)
    return None


def test_bulk_family_recovery_no_garbage():
    """Across whole JCH-dense HVSC dirs, every recognised family tune is coherent.

    Walks a bounded, deterministic set of directories: any tune the family
    reader accepts must yield a coherent model (never garbage), and a healthy
    number must be recovered (proving the walk actually ran against real tunes).
    """
    base = None
    for env in ("HVSC", "JCH_LOCAL_HVSC"):
        cand = os.environ.get(env)
        if cand and Path(cand).is_dir():
            base = Path(cand)
            break
    if base is None:
        pytest.skip("no local HVSC tree for the bulk corpus walk")

    recovered = 0
    checked = 0
    for rel in BULK_DIRS:
        root = base / rel
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.sid")):
            checked += 1
            data = path.read_bytes()
            try:
                model = reader.parse(data)
            except SidParseError:
                continue
            if isinstance(model, NewPlayerModel):
                _assert_coherent_model(model, load=model.load_addr)
                recovered += 1
    if checked == 0:
        pytest.skip("bulk dirs present but empty")
    assert recovered >= 50, f"expected many family recoveries, got {recovered}"
