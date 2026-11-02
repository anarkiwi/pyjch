"""Why the V20 spec cannot play a tune the V20 code build plays (the tune goes to EmuPlayer).

The spec keeps the player's variables as state with no address, so a tune whose tables lie
over one, or whose voices are not placed as the spec places them, is refused.  A note past the
pitch table reads the variable there through the layout's ``wild`` instead.
"""

from typing import Optional, Set

from pyjch.constants import (
    NOTES,
    OVERLAP,
    SHARED_BYTES,
    VOICE_BYTES,
    VOICE_CELLS,
    VOICE_REGISTERS,
    VOICE_WORDS,
)

PITCH_BYTES = 2 * NOTES


def work_bytes(base: int) -> Set[int]:
    """The addresses of the work area at ``base`` the player writes."""
    offsets = set(SHARED_BYTES.values())
    for voice in range(3):
        offsets.update(off + voice for off in VOICE_BYTES.values())
        offsets.update(off + voice for pair in VOICE_WORDS.values() for off in pair)
    return {(base + off) & 0xFFFF for off in offsets}


def data_bytes(bases) -> Set[int]:
    """The addresses of every table the spec reads or patches, to the extent an index reaches."""
    extents = (
        (bases.pitch, PITCH_BYTES),
        (bases.wave_note, 256),
        (bases.wave_ctrl, 256),
        (bases.filterprog, 256),
        (bases.pwprog, 256),
        (bases.instruments, 256),
        (bases.patternptr_lo, 256),
        (bases.patternptr_hi, 256),
        (bases.cmdparam, 128),
        (bases.rearm_ad, 1),
        (bases.rearm_sr, 1),
    )
    return {(at + k) & 0xFFFF for at, size in extents for k in range(size)}


def _image_byte(model, addr: int) -> int:
    at = addr - model.load_addr
    return model.image[at] if 0 <= at < len(model.image) else 0


def _voice_cells(model) -> bool:
    """Whether each voice's register offset and filter masks are the spec's."""
    at = model.init_addr + VOICE_REGISTERS
    return all(
        (
            _image_byte(model, at + v),
            _image_byte(model, at - 6 + v),
            _image_byte(model, at - 3 + v),
        )
        == (7 * v, 1 << v, 0xFF ^ (1 << v))
        for v in range(3)
    )


def refusal(model, bases) -> Optional[str]:
    """The reason the V20 spec cannot play ``model`` (whose code build is ``bases``), or None."""
    if not _voice_cells(model):
        return VOICE_CELLS
    if work_bytes(model.init_addr) & data_bytes(bases):
        return OVERLAP
    return None
