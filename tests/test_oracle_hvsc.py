"""Byte-exact comparison of the JchPlayer render against the sidtrace oracle.

Marked ``oracle`` (needs the Docker ``anarkiwi/sidtrace`` image + HVSC access);
a dedicated CI job runs ``pytest -m oracle``.  Never skipped: an unavailable
tune or a failed oracle render fails the test.  HVSC ``.sid`` files are
copyright works -- cached or read from a local tree, never committed.

Both byte-exact JCH driver versions are covered: **V0x** (a :class:`Song`) and
**V20** (a :class:`NewPlayerModel`), each with real HVSC representatives.  The
JCH NewPlayer is a single-speed PAL 50 Hz player, so the oracle grid is framed
at the fixed PAL cadence -- the sidtrace auto-cadence over-estimates on these
tunes because they change SID registers only every few frames.
"""

import pytest
from pysidtracker import PAL_CYCLES_PER_FRAME, aligned_match

from pyjch import reader
from pyjch.player import JchPlayer

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


@pytest.mark.oracle
@pytest.mark.parametrize("relpath", list(TUNES.values()), ids=list(TUNES))
def test_render_matches_oracle(relpath, hvsc, sidtrace_oracle):
    """The unified player reproduces the sidtrace oracle frame-for-frame."""
    path = hvsc(relpath)
    oracle = sidtrace_oracle(path, frames=FRAMES, cycles_per_frame=PAL_CYCLES_PER_FRAME)
    rendered = JchPlayer(reader.parse(path.read_bytes())).render_grid(len(oracle) + 4)
    assert aligned_match(oracle, rendered, max_lead=4), f"{relpath}: render != oracle"
