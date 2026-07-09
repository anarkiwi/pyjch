"""SID register write logs.

A register log is the player's output flattened to timed chip writes:
one :class:`RegWrite` per SID register write, with an absolute clock in
C64 CPU cycles.  Logs serialize to plain text, one ``clock reg val``
triple per line (decimal, space separated, ``#`` comments allowed), so
they load directly into pandas or any line-based tooling.  This is the
mandatory validator surface deplayroutine's harness imports.
"""

import io
from pathlib import Path
from typing import IO, Iterable, Iterator, NamedTuple

from pyjch import constants, v20player
from pyjch.errors import JCHError, SidParseError
from pyjch.model import Song
from pyjch.newplayer import NewPlayerModel
from pyjch.player import Player


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


# Cycles between consecutive writes within one frame, approximating the
# store instructions of the 6502 playroutine.
DEFAULT_WRITE_SPACING = 16

REGLOG_HEADER = "# pyjch register log: clock reg val"


class RegWrite(NamedTuple):
    """One SID register write at an absolute CPU clock (in cycles)."""

    clock: int
    reg: int
    val: int


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
    # play calls start one frame later, so the init->play gap exceeds one
    # frame and the oracle framing anchors frame 0 to the first play call.
    for frame in range(max_frames):
        writes = player.play_frame()
        clock = (frame + 1) * cycles_per_frame
        for offset, (reg, val) in enumerate(writes):
            yield RegWrite(clock + offset * write_spacing, reg, val)


def write_reglog(writes: Iterable[RegWrite], dst, header: bool = True) -> None:
    """Write a register log to a path or text file-like object."""

    def _dump(out: IO[str]) -> None:
        if header:
            print(REGLOG_HEADER, file=out)
        for write in writes:
            print(f"{write.clock} {write.reg} {write.val}", file=out)

    if isinstance(dst, (str, Path)):
        with open(dst, "w", encoding="utf-8") as out:
            _dump(out)
        return
    _dump(dst)


def read_reglog(src) -> list[RegWrite]:
    """Read a register log from a path or text file-like object."""
    if isinstance(src, (str, Path)):
        text = Path(src).read_text(encoding="utf-8")
    elif isinstance(src, io.IOBase) or hasattr(src, "read"):
        text = src.read()
    else:
        raise TypeError(f"cannot read a register log from {type(src).__name__}")
    writes = []
    for num, line in enumerate(text.splitlines(), start=1):
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        fields = line.split()
        if len(fields) != 3:
            raise JCHError(f"bad register log line {num}: {line!r}")
        try:
            writes.append(RegWrite(*(int(field) for field in fields)))
        except ValueError as exc:
            raise JCHError(f"bad register log line {num}: {line!r}") from exc
    return writes
