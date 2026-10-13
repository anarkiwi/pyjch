"""pyjch's renderer for the ``pysidtracker.survey`` whole-song HVSC oracle survey.

    PYTHONPATH=scripts python -m pysidtracker.survey run $HVSC tunes.csv \
        --work .hvsc-survey --render oracle_hvsc:render
"""

import numpy as np

from pysidtracker.cadence import playroutine_cadence
from pysidtracker.registers import PAL_CYCLES_PER_FRAME

from pyjch import JchPlayer, Song, parse

PLAYER = r"^\(?jch_newplayer(_v\w+)?\)?$"


def render(data, song, nframes):
    """Render 1-based ``song`` of a JCH NewPlayer ``.sid`` and the build it decoded."""
    model = parse(data)
    player = JchPlayer(model, subtune=song - 1)
    cadence = playroutine_cadence(data, subtune=song - 1)
    info = {
        "engine": player.engine,
        "version": "V0x" if isinstance(model, Song) else model.version,
        "rsid": data[:4] == b"RSID",
        "trigger": cadence.source and cadence.source.value,
        "speed": round(PAL_CYCLES_PER_FRAME / cadence.cycles_per_call),
    }
    return np.asarray(player.render_grid(nframes), np.int16), info
