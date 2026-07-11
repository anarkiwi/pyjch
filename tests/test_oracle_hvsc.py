"""Byte-exact comparison of the JchPlayer render against the sidtrace oracle.

Marked ``oracle``: these tests need Docker (the ``anarkiwi/sidtrace`` image) and
network access to HVSC, so the default suite excludes them (see ``pyproject``); a
dedicated CI job runs ``pytest -m oracle``.  They are never skipped -- an
unavailable tune or a failed oracle render fails the test rather than hiding a
regression.  HVSC ``.sid`` files are copyright works: they are downloaded to a
cache (or a local ``$HVSC`` tree), never committed.

Both byte-exact JCH driver versions are covered: **V0x** (a :class:`Song`) and
**V20** (a :class:`NewPlayerModel`), each with real HVSC representatives.  The
JCH NewPlayer is a single-speed PAL 50 Hz player, so the oracle grid is framed
at the fixed PAL cadence -- the sidtrace auto-cadence over-estimates on these
tunes because they change SID registers only every few frames.
"""

import os
from pathlib import Path

import pytest

from pysidtracker.oracle import (
    aligned_match,
    read_sidtrace,
    run_sidtrace,
    sidtrace_grid,
)
from pysidtracker.registers import PAL_CYCLES_PER_FRAME
from pysidtracker.testing import TuneFetchError, resolve_tune

from pyjch import reader
from pyjch.player import JchPlayer

# Cache under the workspace (a Docker-daemon-visible path, and what CI persists
# via actions/cache). ``$PYJCH_ORACLE_CACHE`` overrides the location.
_CACHE = Path(os.environ.get("PYJCH_ORACLE_CACHE", ".oracle-cache"))
_HVSC = _CACHE / "hvsc"
_CSV = _CACHE / "csv"

# One+ HVSC representative per byte-exact JCH driver version, each verified to
# render frame-exact against the sidtrace oracle.
#   V0x  -> pyjch.model.Song, the FUN_1060/FUN_10e8 routine
#   V20  -> pyjch.newplayer.NewPlayerModel, the two-column wavetable engine
#           (24th_Amaranth_Grand_Prix_3 is the DRAX worktune V20 RE reference)
TUNES = {
    "v0x_flexible": "MUSICIANS/S/Scorpio/Flexible.sid",
    "v0x_simple_tune": "MUSICIANS/J/JCH/Simple_Tune.sid",
    "v20_24th_amaranth": "MUSICIANS/D/DRAX/Worktunes/24th_Amaranth_Grand_Prix_3.sid",
    "v20_7d_funkt": "DEMOS/0-9/7D_Funkt.sid",
    "v20_stories": "MUSICIANS/D/DRAX/Stories.sid",
}
FRAMES = 250


def _oracle_grid(path):
    """PAL-framed sidtrace oracle grid for ``path`` (CSV cached per tune)."""
    csv = _CSV / f"{Path(path).stem}.csv.zst"
    if not csv.exists():
        run_sidtrace(path, csv, seconds=FRAMES // 50 + 2)
    grid = sidtrace_grid(read_sidtrace(csv), cycles_per_frame=PAL_CYCLES_PER_FRAME)
    return grid[:FRAMES]


@pytest.fixture(params=list(TUNES))
def tune_id(request):
    return request.param


@pytest.mark.oracle
def test_render_matches_oracle(tune_id):
    """The unified player reproduces the sidtrace oracle frame-for-frame."""
    path = resolve_tune(TUNES[tune_id], cache_dir=_HVSC, local_env="HVSC")
    if path is None:
        raise TuneFetchError(f"tune {tune_id} unavailable (offline, not cached)")
    oracle = _oracle_grid(path)
    rendered = JchPlayer(reader.parse(Path(path).read_bytes())).render_grid(
        len(oracle) + 4
    )
    assert aligned_match(
        oracle, rendered, max_lead=4
    ), f"tune {tune_id}: player render does not match the sidtrace oracle"
