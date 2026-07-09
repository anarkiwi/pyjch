"""Real-HVSC corpus test for the JCH NewPlayer reader.

pyjch models one player: the canonical ``JCH_NewPlayer_V0x`` layout (immediate
attack/decay + sustain/release init, immediate gate-on/gate-off CTRL, split
subpattern pointer tables).  HVSC contains at least twenty *other* JCH
``NewPlayer`` binaries (``sidid`` tags V1..V21, ``Glover_NewPlayer_V21`` and
``JCH_DigiPlayer``); they are genuinely different players -- their init does not
seed AD/SR from immediates at all -- so this reader must **reject** them rather
than read garbage out of a foreign player's code.

This test drives that contract against a real HVSC tree:

* every supported (V0x-canonical) tune parses, is recognised, and is detected
  ``DIRECT``;
* a deterministic representative of every other version is rejected with a
  clear :class:`~pyjch.errors.SidParseError` and is *not* recognised.

Tunes are HVSC copyright works, never committed.  Each tune is resolved from a
local HVSC tree (``$HVSC`` or ``$JCH_LOCAL_HVSC``) or the gitignored fetch
cache; set ``PYJCH_FETCH_CORPUS=1`` to allow on-demand download.  A tune that
cannot be resolved is skipped, so the suite passes offline and runs for real
when ``$HVSC`` points at a local tree.  See ``docs/versions.md`` for the full
per-version HVSC census this sample is drawn from.
"""

import os
import sys
from pathlib import Path

import pytest

from pyjch import reader
from pyjch.errors import SidParseError
from pyjch.reader import JchSidParser
from pysidtracker import SidImage
from pysidtracker.detect import PlayroutineKind

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import fetch_tunes  # noqa: E402  (after sys.path tweak)

# The complete supported set: every HVSC tune whose player is the canonical
# JCH_NewPlayer_V0x layout this reader/player models byte-exactly (proven
# against the preframr-sidtrace oracle in tests/fixtures/*.grid.txt).
SUPPORTED = {
    "Flexible/Scorpio (V0x)": "MUSICIANS/S/Scorpio/Flexible.sid",
    "Simple_Tune/JCH (V0x)": "MUSICIANS/J/JCH/Simple_Tune.sid",
}

# Sidid-tagged V0x tunes that share the V0x init signature (so recognize()
# truthfully matches) but drive gate-on waveforms from an instrument table
# rather than an immediate CTRL -- a routine pyjch does not model, so parse
# must still reject them.
REJECTED_V0X_VARIANT = {
    "V0x-table-variant/Problems": "MUSICIANS/J/JCH/Problems.sid",
    "V0x-table-variant/Imagination": "MUSICIANS/J/JCH/Imagination.sid",
}

# One deterministic representative per NON-canonical JCH NewPlayer version
# present in HVSC (sidid-bucketed).  Each is a genuinely different player
# (its init does not seed AD/SR from immediates), so it is neither recognised
# nor parsed.
REJECTED = {
    "V1": "MUSICIANS/J/JCH/Beatbassie.sid",
    "V2": "MUSICIANS/J/JCH/Caverns.sid",
    "V3": "MUSICIANS/J/JCH/2cVee.sid",
    "V4": "MUSICIANS/J/JCH/Diflexing.sid",
    "V5": "DEMOS/UNKNOWN/Falcon_Intro_03_v1.sid",
    "V6": "MUSICIANS/J/JCH/Acid_1988.sid",
    "V7": "MUSICIANS/J/JCH/Lonewolf.sid",
    "V8": "MUSICIANS/D/DRAX/King_Tut.sid",
    "V9": "MUSICIANS/D/DRAX/Acid.sid",
    "V10": "MUSICIANS/D/DRAX/Ballmania_1st_version.sid",
    "V11": "MUSICIANS/B/Bjerregaard_Johannes/USA_Tune.sid",
    "V12": "MUSICIANS/D/DRAX/Exorcist_game.sid",
    "V13": "MUSICIANS/A/Abaddon/Apina.sid",
    "V14": "DEMOS/A-F/Fjellaporna.sid",
    "V15": "MUSICIANS/A/Ahz_The_Demon/Lunardive.sid",
    "V17": "DEMOS/0-9/1st_Chaff.sid",
    "V18": "MUSICIANS/A/Avalon/Bambino.sid",
    "V19": "DEMOS/G-L/I2_Bass_Loop.sid",
    "V20": "DEMOS/0-9/7D_Funkt.sid",
    "Glover_NewPlayer_V21": "DEMOS/M-R/Mendelssohn.sid",
    "Dane_NewPlayer": "MUSICIANS/M/Mitch_and_Dane/Dane/Au_Revoir.sid",
    "JCH_DigiPlayer": "MUSICIANS/J/JCH/Easy_Does_It.sid",
}


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


@pytest.mark.parametrize("relpath", SUPPORTED.values(), ids=list(SUPPORTED))
def test_supported_version_parses(relpath):
    """Every canonical V0x tune parses, recognises, and detects DIRECT."""
    data = _load(relpath)
    song = reader.parse(data)
    assert song.load_addr == 0x1000
    assert 0 <= song.init_ad <= 0xFF
    assert 0 <= song.init_sr <= 0xFF
    assert song.load_addr <= song.subptr_lo
    assert song.load_addr <= song.subptr_hi
    assert reader.read(data).image == song.image

    parser = JchSidParser()
    assert parser.recognize(SidImage.from_bytes(data)) is not None
    assert parser.detect(data, init=False).kind is PlayroutineKind.DIRECT


@pytest.mark.parametrize("relpath", REJECTED.values(), ids=list(REJECTED))
def test_other_version_rejected(relpath):
    """A representative of every other JCH NewPlayer version is cleanly rejected."""
    data = _load(relpath)
    with pytest.raises(SidParseError):
        reader.parse(data)
    # A foreign player: not the canonical V0x layout, so not even recognised.
    assert JchSidParser().recognize(SidImage.from_bytes(data)) is None


@pytest.mark.parametrize(
    "relpath", REJECTED_V0X_VARIANT.values(), ids=list(REJECTED_V0X_VARIANT)
)
def test_v0x_table_variant_rejected(relpath):
    """A V0x-family tune with a table-driven gate routine parses to a clean error.

    recognize() truthfully matches the shared V0x init signature, but parse
    rejects the unsupported layout instead of emitting garbage.
    """
    data = _load(relpath)
    assert JchSidParser().recognize(SidImage.from_bytes(data)) is not None
    with pytest.raises(SidParseError):
        reader.parse(data)
