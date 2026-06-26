"""Read and play JCH NewPlayer SID songs (byte-exact register output)."""

from pyjch.errors import JCHError, SidParseError
from pyjch.model import Song
from pyjch.player import Player, iter_frames, render_grid
from pyjch.reader import parse, read
from pyjch.reglog import (
    RegWrite,
    iter_register_writes,
    read_reglog,
    write_reglog,
)

__version__ = "0.1.0"

__all__ = [
    "JCHError",
    "Player",
    "RegWrite",
    "SidParseError",
    "Song",
    "__version__",
    "iter_frames",
    "iter_register_writes",
    "parse",
    "read",
    "read_reglog",
    "render_grid",
    "write_reglog",
]
