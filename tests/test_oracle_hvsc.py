"""Byte-exact comparison of the JchPlayer render against the sidtrace oracle.

Marked ``oracle`` (needs the Docker ``anarkiwi/sidtrace`` image + HVSC access);
a dedicated CI job runs ``pytest -m oracle``.  Never skipped: an unavailable
tune or a failed oracle render fails the test.  HVSC ``.sid`` files are
copyright works -- cached or read from a local tree, never committed.

Every recovered JCH NewPlayer driver version is covered, at 60 seconds each:

* **V0x** (a :class:`Song`) and **V20** (a :class:`NewPlayerModel`) render
  through their native pure-Python engines;
* every other recovered family version renders through
  :class:`~pysidtracker.EmuPlayer` -- the tune's own 6502 driver run on py65.

Each tune is framed at its own clock (``cycles_per_frame_for_flags`` reads the
PSID clock bits: most JCH tunes are PAL, a few NTSC), because the JCH player is
single-speed and the sidtrace auto-cadence over-estimates when a tune changes
SID registers only every few frames.
"""

import pytest
from pysidtracker import aligned_match, cycles_per_frame_for_flags
from pysidtracker.image import SidImage

from pyjch import reader
from pyjch.player import JchPlayer

# One+ HVSC representative per recovered JCH NewPlayer driver version, each
# verified to render register-exact against the sidtrace oracle over 60 s.
#   V0x        -> pyjch.model.Song native engine (FUN_1060/FUN_10e8)
#   V20        -> pyjch.newplayer.NewPlayerModel native two-column engine
#                 (24th_Amaranth_Grand_Prix_3 is the DRAX worktune V20 RE ref)
#   V1..V18    -> EmuPlayer: the tune's own driver run on py65
TUNES = {
    "v0x_flexible": "MUSICIANS/S/Scorpio/Flexible.sid",
    "v0x_simple_tune": "MUSICIANS/J/JCH/Simple_Tune.sid",
    "v20_24th_amaranth": "MUSICIANS/D/DRAX/Worktunes/24th_Amaranth_Grand_Prix_3.sid",
    "v20_7d_funkt": "DEMOS/0-9/7D_Funkt.sid",
    "v20_stories": "MUSICIANS/D/DRAX/Stories.sid",
    "v1_beatbassie": "MUSICIANS/J/JCH/Beatbassie.sid",
    "v2_caverns": "MUSICIANS/J/JCH/Caverns.sid",
    "v6_acid_1988": "MUSICIANS/J/JCH/Acid_1988.sid",
    "v8_king_tut": "MUSICIANS/D/DRAX/King_Tut.sid",
    "v9_acid": "MUSICIANS/D/DRAX/Acid.sid",
    "v10_ballmania": "MUSICIANS/D/DRAX/Ballmania_1st_version.sid",
    "v11_usa_tune": "MUSICIANS/B/Bjerregaard_Johannes/USA_Tune.sid",
    "v13_apina": "MUSICIANS/A/Abaddon/Apina.sid",
    "v14_fjellaporna": "DEMOS/A-F/Fjellaporna.sid",
    "v15_lunardive": "MUSICIANS/A/Ahz_The_Demon/Lunardive.sid",
    "v17_1st_chaff": "DEMOS/0-9/1st_Chaff.sid",
    "v18_bambino": "MUSICIANS/A/Avalon/Bambino.sid",
}
SECONDS = 60


@pytest.mark.oracle
@pytest.mark.parametrize("relpath", list(TUNES.values()), ids=list(TUNES))
def test_render_matches_oracle(relpath, hvsc, sidtrace_oracle):
    """The player reproduces the sidtrace oracle register-for-register, 60 s."""
    path = hvsc(relpath)
    data = path.read_bytes()
    cycles = cycles_per_frame_for_flags(SidImage.from_bytes(data).header.flags)
    oracle = sidtrace_oracle(
        path, frames=None, seconds=SECONDS, cycles_per_frame=cycles
    )
    rendered = JchPlayer(reader.parse(data)).render_grid(len(oracle) + 4)
    assert aligned_match(oracle, rendered, max_lead=4), f"{relpath}: render != oracle"
