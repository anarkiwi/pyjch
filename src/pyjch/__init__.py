"""Read and play JCH NewPlayer SID songs (byte-exact register output)."""

from pyjch.editor import NP20_PROFILE, Np20Profile, write_editor_prg
from pyjch.errors import JCHError, SidParseError
from pyjch.extract import extract
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
from pyjch.serialize import from_json, to_json, to_text
from pyjch.songmodel import Tune
from pyjch.v20player import V20Player, playable as v20_playable

__version__ = "0.1.0"

__all__ = [
    "JCHError",
    "JchSidParser",
    "NP20_PROFILE",
    "NewPlayerModel",
    "Np20Profile",
    "Player",
    "RegWrite",
    "SidParseError",
    "Song",
    "Tune",
    "V20Player",
    "__version__",
    "extract",
    "from_json",
    "iter_frames",
    "iter_register_writes",
    "make_player",
    "parse",
    "read",
    "read_reglog",
    "render_grid",
    "to_json",
    "to_text",
    "v20_playable",
    "write_editor_prg",
]
