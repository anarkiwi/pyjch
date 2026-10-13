"""The host routine a NewPlayer core runs under, when it is not the PSID driver.

A tune whose container entries are not the core's own vectors drives the core
from a host of its own.  The one host with state of its own in HVSC runs the
core with I/O banked out, flushes the 25 shadowed registers itself, overrides
pulse width, cutoff, resonance/routing and volume from a four-byte record per
frame, and stops after a two-byte frame countdown.  :func:`wrapper` reads that
host's cells and immediates from the operands of its own code.
"""

from typing import Dict, Optional

from pysidtracker import CodePattern, find_code_all

_WRAPPER = tuple(
    CodePattern(spec)
    for spec in (
        # play: the gate, the core and the shadow copy, then the frame countdown
        "A9 ?? F0 01 60 20 ?? ?? 20 ?? ?? A2 ?? CA D0 0B A0 ?? 88 D0 03"
        " EE {gate:w} 8C {count_hi:w} 8E {count_lo:w} 60",
        # the banked core call, the shadow copy and the per-frame overrides
        "EE ?? ?? A9 34 85 ?? 20 ?? ?? A2 18 BD {ghost:w} 95 {buf} CA 10 F8 A9 35 85 ??"
        " A9 {vol_value} 85 {vol_reg} A9 {res_value} 85 {res_reg} 20 ?? ?? AD ?? ??"
        " 20 ?? ?? 86 {pw0_lo} 85 {pw0_hi} AD ?? ?? 20 ?? ?? 86 {pw1_lo} 85 {pw1_hi}"
        " AD ?? ?? 85 {cut_reg} 60",
        # the record: four bytes through a zero-page pointer, then the pointer steps
        "A0 00 B1 {record_ptr} 8D {pw0:w} A0 01 B1 ?? 8D {pw1:w} A0 02 B1 ?? 8D {cut:w}"
        " A0 03 B1 ?? 8D {delay:w} A5 ?? 18 69 04 85 ?? 90 02 E6 ?? 60",
        # the flush: 25 registers, in the direction the record's delay byte picks
        "AD {flush_delay:w} F0 11 A2 18 B5 ?? AC ?? ?? 88 10 FD 9D ?? ?? CA 10 F2 60",
    )
)


def wrapper(image: bytes) -> Optional[Dict[str, int]]:
    """The banked-I/O host's cells and immediates, or ``None`` without one.

    Every fragment must occur exactly once; ``buf``/``*_reg``/``pw*_lo/hi`` and
    ``record_ptr`` are zero-page addresses, ``*_value`` immediates, the rest
    absolute cells.
    """
    found = [find_code_all(image, pattern) for pattern in _WRAPPER]
    if any(len(matches) != 1 for matches in found):
        return None
    return {k: v for matches in found for k, v in matches[0].captures.items()}
