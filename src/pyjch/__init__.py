"""Read and play JCH NewPlayer SID songs (byte-exact register output)."""

from pyjch.editor import (
    NP20_PROFILE,
    NP21_PROFILE,
    NP22_PROFILE,
    NP23_PROFILE,
    NP24_PROFILE,
    NP25_PROFILE,
    NP2X_PROFILE,
    EditorProfile,
    np_profile,
    write_editor_prg,
)
from pyjch.errors import JCHError, SidParseError
from pyjch.extract import extract
from pyjch.model import Song
from pyjch.newplayer import NewPlayerModel
from pyjch.player import JchPlayer, iter_frames, playable, render_grid
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

__version__ = "0.3.0"

__all__ = [
    "EditorProfile",
    "JCHError",
    "JchPlayer",
    "JchSidParser",
    "NP20_PROFILE",
    "NP21_PROFILE",
    "NP22_PROFILE",
    "NP23_PROFILE",
    "NP24_PROFILE",
    "NP25_PROFILE",
    "NP2X_PROFILE",
    "NewPlayerModel",
    "RegWrite",
    "SidParseError",
    "Song",
    "Tune",
    "__version__",
    "extract",
    "from_json",
    "iter_frames",
    "iter_register_writes",
    "make_player",
    "np_profile",
    "parse",
    "playable",
    "read",
    "read_reglog",
    "render_grid",
    "to_json",
    "to_text",
    "write_editor_prg",
]
