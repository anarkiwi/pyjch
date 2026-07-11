"""Shared test fixtures: HVSC tune resolution.

Tunes are HVSC copyright works, never committed.  ``tune_path`` resolves a
V0x reference tune from a local HVSC tree (``$JCH_LOCAL_HVSC``/``$HVSC``) if
present, else fetches it from the public HVSC mirror into a gitignored cache
(reused on later runs).  The tests always run -- a genuinely unreachable tune is
a hard failure, never a silent skip.  Byte-exact validation lives in
``tests/test_oracle_hvsc.py`` (the shared ``pysidtracker`` sidtrace oracle,
marked ``oracle``).
"""

import sys
from pathlib import Path

import pytest

from pysidtracker.testing import TuneFetchError, resolve_tune

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import fetch_tunes  # noqa: E402  (after sys.path tweak)

# id -> HVSC path for the canonical V0x reference tunes the player unit tests
# and the register-log tests exercise offline (once cached).
TUNE_IDS = ("flexible", "simple")


@pytest.fixture(params=TUNE_IDS)
def tune_id(request):
    """Parametrize over each JCH NewPlayer V0x reference tune id."""
    return request.param


@pytest.fixture
def tune_path(tune_id):
    """Path to the tune for ``tune_id`` (local tree, cache, or mirror fetch)."""
    path = resolve_tune(
        fetch_tunes.TUNES[tune_id],
        cache_dir=fetch_tunes.CACHE,
        local_env="JCH_LOCAL_HVSC",
    )
    if path is None:
        raise TuneFetchError(f"tune {tune_id} unavailable (offline, not cached)")
    return path
