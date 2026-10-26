"""pyjch's renderer for the ``pysidtracker.survey`` whole-song HVSC oracle survey.

    PYTHONPATH=scripts python -m pysidtracker.survey run $HVSC tunes.csv \
        --work .hvsc-survey --render oracle_hvsc:render
"""

import numpy as np

from pyjch import make_player, parse
from pyjch.cli_plugin import FORMAT

PLAYER = r"^\(?jch_newplayer(_v\w+)?\)?$"


def render(data, song, nframes):
    """Render 1-based ``song`` of a JCH NewPlayer ``.sid`` and the build it decoded."""
    model = parse(data)
    player = make_player(model, subtune=song - 1)
    info = FORMAT.info(model, data, song)
    return np.asarray(player.render_grid(nframes), np.int16), info
