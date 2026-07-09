"""Read and play JCH NewPlayer SID songs (byte-exact register output)."""

from pyjch.errors import JCHError, SidParseError
from pyjch.model import Song
from pyjch.newplayer import NewPlayerModel
from pyjch.player import Player, iter_frames, render_grid
from pyjch.reader import JchSidParser, parse, read
from pyjch.reglog import (
    RegWrite,
    iter_register_writes,
    make_player,
    read_reglog,
    write_reglog,
)
from pyjch.v20player import V20Player, playable as v20_playable

__version__ = "0.1.0"

__all__ = [
    "JCHError",
    "JchSidParser",
    "NewPlayerModel",
    "Player",
    "RegWrite",
    "SidParseError",
    "Song",
    "V20Player",
    "__version__",
    "iter_frames",
    "iter_register_writes",
    "make_player",
    "parse",
    "read",
    "read_reglog",
    "render_grid",
    "v20_playable",
    "write_reglog",
]
