"""Decompile a JCH NewPlayer ``.sid``: the song, how it is built, and its header.

The NewPlayer core is found by its own ``JMP init ; JMP play`` vectors wherever
it sits, whatever drives it: the PSID driver directly, a host routine that
wraps it, or an interrupt the tune installs itself.  A tune whose core is not
in the loaded image (crunched, or moved by its init) has its init run on a
6502 first and the core recovered from the resulting memory.
"""

import dataclasses
from dataclasses import dataclass
from typing import Dict, Optional

from pysidtracker import (
    PlayroutineKind,
    SidHeader,
    SidImage,
    detect_playroutine,
    read_bytes,
)
from pysidtracker import SidParseError as _BaseSidParseError
from pysidtracker.emu import wire_mpu

from pyjch.errors import SidParseError
from pyjch.host import wrapper
from pyjch.model import Song
from pyjch.newplayer import NewPlayerModel, core_candidates, core_shape, vector
from pyjch.player import V20Bases, discover_bases, playable
from pyjch.reader import parse_image
from pyjch.song import JchSong, decode


@dataclass
class JchInfo:
    """How a decompiled tune is built.

    ``version`` is the core's play-prologue class (``V0x`` for the canonical
    layout); ``kind`` whether the core was found as loaded or only after its
    init ran; ``host`` how the core is driven (``psid``, ``wrapper`` or
    ``irq``); ``voices`` the tracks the core sequences; ``native`` whether a
    pure-Python engine replays it (else the tune's own code does, on jennings);
    ``bases`` every table and the core's code base for a V20 code build; and
    ``wrapper`` the banked-I/O host's cells (:mod:`pyjch.host`), when present;
    ``v0x`` the V0x build's seeded envelope and gate bytes.
    """

    # pylint: disable=too-many-instance-attributes  # one field per build fact
    version: str
    kind: PlayroutineKind
    host: str
    code_base: Optional[int]
    voices: int
    native: bool
    bases: Optional[V20Bases] = None
    wrapper: Optional[Dict[str, int]] = None
    v0x: Optional["V0xBuild"] = None


@dataclass(frozen=True)
class V0xBuild:
    """The V0x player's seeded attack/decay, sustain/release and gate CTRL bytes."""

    attack_decay: int
    sustain_release: int
    gate_on: int
    gate_off: int


@dataclass
class DecompiledSid:
    """A decompiled ``.sid``: the typed song, its build, and the located layout.

    ``layout`` is the reader's located model (addresses and memory) that
    :class:`~pyjch.player.JchPlayer` runs; the song carries neither.
    """

    song: JchSong
    info: JchInfo
    header: Optional[SidHeader]
    layout: object
    subtune: int = 0


def _info(song, kind: PlayroutineKind, host: str) -> JchInfo:
    if isinstance(song, Song):
        build = V0xBuild(song.init_ad, song.init_sr, song.gate_ctrl, song.gateoff_ctrl)
        return JchInfo("V0x", kind, host, None, 3, True, v0x=build)
    return JchInfo(
        version=song.version,
        kind=kind,
        host=host,
        code_base=song.code_base,
        voices=song.voices,
        native=playable(song) is not None,
        bases=discover_bases(song.load_addr, song.image, song),
        wrapper=wrapper(song.image) if host == "wrapper" else None,
    )


def entered_core(image: SidImage, subtune: int, max_cycles: int = 2_000_000):
    """The core the tune's own init enters for ``subtune``, traced on jennings.

    An image may carry one core per song, its init dispatching on the song
    number; the first core vector or init routine the init reaches is the one.
    ``None`` when the image holds at most one core, or init reaches none.
    """
    load, raw = image.load, image.image
    init = image.header.init_address or load if image.header else load
    cores = [
        cb
        for cb, _ in core_candidates(load, raw, init)
        if core_shape(load, raw, cb) is not None
    ]
    if len(cores) < 2:
        return None
    entry = {cb: cb for cb in cores} | {vector(load, raw, cb): cb for cb in cores}
    mpu, mem = wire_mpu(bytearray(image.mem))
    mem[0x1FF], mem[0x1FE], mpu.sp = 0x00, 0x01, 0xFD
    mpu.a, mpu.pc = subtune, init
    while mpu.sp < 0xFF and mpu.processorCycles < max_cycles:
        if mpu.pc in entry:
            return entry[mpu.pc]
        mpu.step()
    return None


def decompile_sid(src, subtune: int = 0) -> DecompiledSid:
    """Decompile a JCH NewPlayer ``.sid``/``.prg`` into a :class:`DecompiledSid`.

    ``src`` may be a path, ``bytes`` or a binary file object.  An image with
    several cores is read through the one the init enters for ``subtune``
    (:func:`entered_core`).  A core found in the loaded image is read
    statically; otherwise the tune's init runs on
    jennings (:func:`pysidtracker.detect_playroutine`) and the core is read from
    the whole of memory, after which its own vectors are its entries.
    """
    try:
        image = SidImage.from_bytes(read_bytes(src))
    except _BaseSidParseError as exc:
        raise SidParseError(str(exc)) from exc
    errors = []
    core = entered_core(image, subtune)

    def recognize(img: SidImage):
        try:  # the second call sees the memory the init left
            return parse_image(img, 0 if errors else None, core)
        except SidParseError as exc:
            errors.append(exc)
            return None

    det = detect_playroutine(image, recognize, subtune=subtune)
    if det.kind is PlayroutineKind.UNKNOWN:
        raise errors[-1]
    song = det.anchor
    host = getattr(song, "host", "psid")
    if det.ran_init and isinstance(song, NewPlayerModel):
        play = song.code_base + 3 if song.host == "irq" else song.play_addr
        song = dataclasses.replace(song, init_addr=song.code_base, play_addr=play)
    return DecompiledSid(
        decode(song)[0], _info(song, det.kind, host), image.header, song, subtune
    )
