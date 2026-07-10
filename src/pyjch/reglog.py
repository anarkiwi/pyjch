"""SID register write logs.

A register log is the player's output flattened to timed chip writes:
one :class:`RegWrite` per SID register write, with an absolute clock in
C64 CPU cycles.  Logs serialize to plain text, one ``clock reg val``
triple per line (decimal, space separated, ``#`` comments allowed), so
they load directly into pandas or any line-based tooling.  This is the
mandatory validator surface deplayroutine's harness imports.
"""

from typing import Iterator

from pysidtracker.reglog import (
    DEFAULT_WRITE_SPACING,
    RegWrite,
    frame_writes,
    read_reglog,
    write_reglog,
)

from pyjch import constants, v20player
from pyjch.errors import JCHError, SidParseError
from pyjch.model import Song
from pyjch.newplayer import NewPlayerModel
from pyjch.player import Player

__all__ = [
    "DEFAULT_WRITE_SPACING",
    "RegWrite",
    "iter_register_writes",
    "make_player",
    "read_reglog",
    "write_reglog",
]


def make_player(song):
    """Return the byte-exact player for ``song``.

    A :class:`~pyjch.model.Song` (canonical V0x) uses :class:`~pyjch.player.Player`;
    a :class:`~pyjch.newplayer.NewPlayerModel` that is a verified **V20** build
    (see :func:`pyjch.v20player.playable`) uses :class:`~pyjch.v20player.V20Player`.
    Any other recovered family model has no byte-exact player yet and raises.
    """
    if isinstance(song, NewPlayerModel):
        if v20player.playable(song) is None:
            raise SidParseError(
                f"{song.version}: song model recovered, but byte-exact playback "
                "is not supported for this JCH NewPlayer family version "
                "(byte-exact players: V0x, V20)"
            )
        return v20player.V20Player(song)
    return Player(song)


def iter_register_writes(
    song: Song,
    max_frames: int = 50 * 60,
    cycles_per_frame: int = constants.PAL_CYCLES_PER_FRAME,
    write_spacing: int = DEFAULT_WRITE_SPACING,
) -> Iterator[RegWrite]:
    """Yield :class:`RegWrite` for ``song``, frame by frame.

    The write-stream mirrors how the player actually runs on a C64, so it
    frames identically to the ``preframr-sidtrace`` oracle: the ``init``
    routine's SID register baseline is emitted once at clock 0, then each
    ``play`` call follows one frame later (a ``> cycles_per_frame`` gap, so
    an oracle framer anchors frame 0 to the first play call and uses the
    init writes as frame 0's baseline -- never mistaking a run of silent
    play frames for the init gap).

    The JCH player loops forever, so ``max_frames`` bounds the log
    (default one minute at 50 Hz).  Writes within a frame are spaced
    ``write_spacing`` cycles from the frame boundary; play frames are
    ``cycles_per_frame`` apart.
    """
    if write_spacing * constants.SID_REGISTERS >= cycles_per_frame:
        raise JCHError("write_spacing too large for one frame")
    player = make_player(song)
    # init baseline burst at clock 0 (the post-init SID register file).
    for offset, val in enumerate(player.regs):
        yield RegWrite(offset * write_spacing, offset, val)

    # play calls start one frame later (start_frame=1), so the init->play gap
    # exceeds one frame and the oracle framing anchors frame 0 to the first
    # play call.  The player already yields 0..24 register offsets, so
    # frame_writes runs with sid_reg_base=0.
    def _play_frames():
        for _ in range(max_frames):
            yield player.play_frame()

    yield from frame_writes(
        _play_frames(),
        cycles_per_frame=cycles_per_frame,
        write_spacing=write_spacing,
        start_frame=1,
        sid_reg_base=0,
    )
