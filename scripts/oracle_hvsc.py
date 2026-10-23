"""pyjch's renderer for the ``pysidtracker.survey`` whole-song HVSC oracle survey.

    PYTHONPATH=scripts python -m pysidtracker.survey run $HVSC tunes.csv \
        --work .hvsc-survey --render oracle_hvsc:render
"""

import numpy as np

from pysidtracker.cadence import cadence_info

from pyjch import Song, engine_of, make_player, parse

PLAYER = r"^\(?jch_newplayer(_v\w+)?\)?$"


def render(data, song, nframes):
    """Render 1-based ``song`` of a JCH NewPlayer ``.sid`` and the build it decoded."""
    model = parse(data)
    player = make_player(model, subtune=song - 1)
    info = {
        "engine": engine_of(player),
        "version": "V0x" if isinstance(model, Song) else model.version,
        **cadence_info(data, song),
    }
    return np.asarray(player.render_grid(nframes), np.int16), info
