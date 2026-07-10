"""Shared test fixtures: tune access and the byte-exact oracle.

Tunes are HVSC copyright works, never committed.  ``tune_path`` resolves a
tune from a local HVSC tree (``$JCH_LOCAL_HVSC``) if present, else fetches
it into a gitignored cache (skipping the test when the tune is absent and
there is no network).  ``oracle_grid`` produces the ground-truth per-frame
SID register grid -- live from the ``preframr-sidtrace`` binary
(``$SIDTRACE_BIN``) when available, else from the committed frozen grid --
mirroring deplayroutine's env-gated validator.

The grid framer / sidwr reader / grid aligner are the shared
``pysidtracker.oracle`` surfaces (``grid_from_writes`` / ``read_sidwr`` /
``aligned_match``), re-exported here for the tune-dependent tests.
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from pysidtracker.oracle import grid_from_writes, read_sidwr
from pysidtracker.testing import resolve_tune

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import fetch_tunes  # noqa: E402  (after sys.path tweak)

FIXTURES = Path(__file__).resolve().parent / "fixtures"

# id -> committed frozen grid file (and the canonical tune id).
TUNE_IDS = ("flexible", "simple")


@pytest.fixture(params=TUNE_IDS)
def tune_id(request):
    """Parametrize over each JCH NewPlayer test tune id."""
    return request.param


@pytest.fixture
def tune_path(tune_id):
    """Path to the tune for ``tune_id``, skipping if unavailable."""
    path = resolve_tune(
        fetch_tunes.TUNES[tune_id],
        cache_dir=fetch_tunes.CACHE,
        local_env="JCH_LOCAL_HVSC",
    )
    if path is None:
        pytest.skip(f"tune {tune_id} unavailable (offline, not cached)")
    return path


def _live_grid(tune_file, nframes=400):
    binary = os.environ.get("SIDTRACE_BIN")
    if not binary or not os.path.exists(binary):
        return None
    with tempfile.TemporaryDirectory() as tmp:
        prefix = os.path.join(tmp, "trace")
        subprocess.run(
            [binary, str(tune_file), "0", str(nframes), prefix],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return grid_from_writes(read_sidwr(prefix + ".sidwr.bin"))


def _frozen_grid(tune_id):
    path = FIXTURES / f"{tune_id}.grid.txt"
    if not path.exists():
        return None
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        rows.append([int(tok, 16) for tok in line.split()])
    return rows


@pytest.fixture
def oracle_grid(tune_id, tune_path):
    """Ground-truth grid: live sidtrace if available, else the frozen grid."""
    grid = _live_grid(tune_path)
    source = "live-sidtrace"
    if grid is None:
        grid = _frozen_grid(tune_id)
        source = "frozen-grid"
    if grid is None:
        pytest.skip(f"no oracle for {tune_id}")
    return grid, source
