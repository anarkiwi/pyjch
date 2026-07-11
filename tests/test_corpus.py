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
local HVSC tree (``$HVSC`` or ``$JCH_LOCAL_HVSC``) or, failing that, fetched from
the public HVSC mirror into the gitignored cache (reused on later runs).  These
tests always run -- here and in CI -- against a deterministic per-version set of
real representatives; a genuinely unreachable tune is a hard failure, never a
silent skip.  See ``docs/versions.md`` for the full per-version HVSC census.
"""

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
def test_supported_version_parses(relpath, hvsc):
    """Every canonical V0x tune parses to a Song, recognises, detects DIRECT."""
    data = hvsc.read(relpath)
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
def test_family_model_recovered(relpath, hvsc):
    """A family representative parses to a coherent NewPlayerModel and recognises."""
    data = hvsc.read(relpath)
    model = reader.parse(data)
    _assert_coherent_model(model)
    assert reader.read(data).subtune_table == model.subtune_table

    parser = JchSidParser()
    assert parser.recognize(SidImage.from_bytes(data)) is not None
    assert parser.detect(data, init=False).kind is PlayroutineKind.DIRECT


@pytest.mark.parametrize("relpath", MODEL_RECOVERED.values(), ids=list(MODEL_RECOVERED))
def test_family_extracts_to_coherent_tune(relpath, hvsc):
    """Every family representative extracts to a bounded, coherent Tune."""
    model = reader.parse(hvsc.read(relpath))
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
def test_other_version_rejected(relpath, hvsc):
    """A representative of every unrecoverable version is cleanly rejected."""
    data = hvsc.read(relpath)
    with pytest.raises(SidParseError):
        reader.parse(data)
    assert JchSidParser().recognize(SidImage.from_bytes(data)) is None


@pytest.mark.parametrize(
    "relpath", REJECTED_V0X_VARIANT.values(), ids=list(REJECTED_V0X_VARIANT)
)
def test_v0x_table_variant_rejected(relpath, hvsc):
    """A V0x-family tune with a table-driven gate routine rejects, not garbage.

    recognize() truthfully matches the shared V0x init signature, but parse
    rejects the unsupported layout instead of emitting garbage.
    """
    data = hvsc.read(relpath)
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
    "V14": {"note": True, "ctrl": True, "pitch": True, "gate": True, "export": True},
    "V15": {"note": True, "ctrl": True, "pitch": True, "gate": True, "export": True},
    "V17": {"note": True, "ctrl": True, "pitch": True, "gate": True, "export": True},
    "V18": {"note": True, "ctrl": True, "pitch": True, "gate": True, "export": True},
    "V20": {"note": True, "ctrl": True, "pitch": True, "gate": True, "export": True},
}


@pytest.mark.parametrize("version", MODEL_RECOVERED, ids=list(MODEL_RECOVERED))
def test_family_table_discovery_matrix(version, hvsc):
    """Each representative reaches exactly its recorded table-discovery tier.

    Asserts the generalized wave-column / pitch idioms recover the tables the
    coverage map records, that the editor table gate passes for the full-table
    versions, and that a full ``$0F00`` editor ``.prg`` (with the wave/pitch
    tables laid) is emitted for those whose data fits the editor format.
    """
    cov = FAMILY_COVERAGE[version]
    model = reader.parse(hvsc.read(MODEL_RECOVERED[version]))
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


# A family tune with a sequence exceeding the 96-row cap: exercises the
# lossless split in the editor writer end-to-end against real data.
SPLIT_REPRESENTATIVE = "MUSICIANS/D/DRAX/Worktunes/KLKLK.sid"


def _voice_sequences(prg, profile, voice):
    """Walk the emitted order list for ``voice`` -> list of chunk-body byte lists."""
    load = prg[0] | (prg[1] << 8)
    img = prg[2:]
    byte = lambda addr: img[addr - load]  # noqa: E731
    word = lambda addr: byte(addr) | (byte(addr + 1) << 8)  # noqa: E731
    seqlo = word(profile.ptr_addr(profile.idx_seqlo))
    seqhi = word(profile.ptr_addr(profile.idx_seqhi))

    def seq_body(idx):
        addr = byte(seqlo + idx) | (byte(seqhi + idx) << 8)
        out = []
        while byte(addr) != 0x7F:
            out.append(byte(addr))
            addr += 1
        return out

    addr = word(profile.ptr_addr(profile.idx_order[voice]))
    bodies = []
    while byte(addr) not in (0xFE, 0xFF):
        if byte(addr) < 0x80:
            bodies.append(seq_body(byte(addr)))
        addr += 1
    return bodies


def test_pattern_split_roundtrip_real_tune(hvsc):
    """A real tune with a >96-row sequence exports; the split is lossless.

    Every emitted sequence is <=96 rows, and walking each voice's emitted order
    list and concatenating the chunk bodies reproduces the original recovered
    per-voice sequence stream byte-for-byte (minus injected $7F terminators).
    """
    tune = extract(reader.parse(hvsc.read(SPLIT_REPRESENTATIVE)))
    # This representative must actually contain an over-cap sequence to split.
    assert any(
        sum(1 for b in raw[:-1] if b < 0x80) > 96 for raw in tune.pattern_raw
    ), "representative no longer exercises the split"
    profile = np_profile(25)
    prg = write_editor_prg(tune, profile=profile)
    for voice in range(3):
        emitted = _voice_sequences(prg, profile, voice)
        for body in emitted:
            assert sum(1 for b in body if b < 0x80) <= 96
        original = [
            b
            for entry in tune.subtunes[0].order_lists[voice].entries
            for b in tune.pattern_raw[entry.pattern][:-1]
        ]
        assert [b for body in emitted for b in body] == original
