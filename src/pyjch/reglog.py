"""SID register write logs -- the shared ``py*`` register-log surface.

A register log is the player's output flattened to timed chip writes: one
:class:`RegWrite` per SID register write, with an absolute clock in C64 CPU
cycles.  Logs serialize to plain text, one ``clock reg val`` triple per line.
The framing (init baseline at clock 0, then per-frame diffs) is
:func:`pysidtracker.reglog.register_writes_from_player`, driven by the pyjch
:class:`~pyjch.player.JchPlayer`; this module only adds the pyjch entry points.
"""

from typing import Iterator

from pysidtracker.reglog import (
    DEFAULT_WRITE_SPACING,
    RegWrite,
    read_reglog,
    register_writes_from_player,
    write_reglog,
)

from pyjch import constants
from pyjch.errors import JCHError
from pyjch.player import JchPlayer

__all__ = [
    "DEFAULT_WRITE_SPACING",
    "RegWrite",
    "iter_register_writes",
    "make_player",
    "read_reglog",
    "write_reglog",
]


def make_player(model) -> JchPlayer:
    """Return the byte-exact :class:`~pyjch.player.JchPlayer` for ``model``.

    A :class:`~pyjch.model.Song` drives the V0x routine; a
    :class:`~pyjch.newplayer.NewPlayerModel` drives the V20 engine when it is a
    verified V20 build.  Any other recovered family model has no byte-exact
    player yet and raises :class:`~pyjch.errors.SidParseError`.
    """
    return JchPlayer(model)


def iter_register_writes(
    model,
    max_frames: int = 50 * 60,
    cycles_per_frame: int = constants.PAL_CYCLES_PER_FRAME,
    write_spacing: int = DEFAULT_WRITE_SPACING,
) -> Iterator[RegWrite]:
    """Yield :class:`RegWrite` for ``model``, frame by frame.

    The write-stream mirrors how the player runs on a C64, so it frames
    identically to the sidtrace oracle: the ``init`` routine's SID register
    baseline is emitted once at clock 0, then each ``play`` call follows one
    frame later.  The JCH player loops forever, so ``max_frames`` bounds the log
    (default one minute at 50 Hz).
    """
    if write_spacing * constants.SID_REGISTERS >= cycles_per_frame:
        raise JCHError("write_spacing too large for one frame")
    yield from register_writes_from_player(
        make_player(model), max_frames, cycles_per_frame, write_spacing
    )
