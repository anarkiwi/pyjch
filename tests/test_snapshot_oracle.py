"""The oracle tunes' model, render and export digests against the committed baseline.

``pysidtracker.snapshot`` digests what the jch format makes of each oracle tune
(its decoded model, its start song's whole-length render, its recovered song
JSON); any digest that differs from ``oracle_snapshot.json`` is a behaviour
change.  ``PYJCH_UPDATE_SNAPSHOT=1`` rewrites the baseline from the current code.
"""

import os
from pathlib import Path

import pytest
from pysidtracker.snapshot import diff, read_snapshot, snapshot, write_snapshot

from tests.test_oracle_hvsc import TUNES

BASELINE = Path(__file__).with_name("oracle_snapshot.json")


@pytest.mark.oracle
def test_oracle_tunes_match_the_baseline_snapshot(hvsc):
    """No oracle tune's model, render or recovered song differs from the baseline."""
    current = snapshot([(name, hvsc(rel)) for name, rel in TUNES.items()], "jch")
    if os.environ.get("PYJCH_UPDATE_SNAPSHOT"):
        write_snapshot(current, BASELINE)
    changed = diff(read_snapshot(BASELINE), current)
    assert not changed, "\n".join(f"{n} {k}: {a} -> {b}" for n, k, a, b in changed)
