"""Typed model of a JCH NewPlayer song.

A :class:`Song` holds everything the reader recovers from a JCH tune
image: the per-tune discovered immediates (the AD/SR defaults and the
gate-on / gate-off CTRL bytes), the relocated subpattern pointer-table
bases, the per-subtune orderlist pointer pairs, the split note-frequency
tables, and the raw whole-image bytes.  The player reads the opcode
streams (orderlists + subpatterns) directly out of ``image`` via the
discovered addresses, exactly as the 6502 player does, so a relocated
tune of the same player plays unchanged.
"""

from dataclasses import dataclass, field
from typing import List

from pyjch import constants


@dataclass
class Song:
    """A complete JCH NewPlayer song.

    ``load_addr`` is the C64 load address; ``image`` the raw whole-image
    bytes (player + data).  ``init_ad`` / ``init_sr`` are the discovered
    attack-decay / sustain-release defaults; ``gate_ctrl`` / ``gateoff_ctrl``
    the discovered gate-on / gate-off (rest) CTRL bytes; ``subptr_lo`` /
    ``subptr_hi`` the (relocated) subpattern pointer-table base addresses;
    ``orderlist_ptr_table`` the absolute base of the per-subtune orderlist
    pointer pairs; ``freq_lo`` / ``freq_hi`` the split note-frequency
    tables.
    """

    # pylint: disable=too-many-instance-attributes  # a full tune is wide
    load_addr: int = constants.DEFAULT_LOAD
    init_addr: int = constants.DEFAULT_INIT
    play_addr: int = constants.DEFAULT_PLAY
    name: str = ""
    author: str = ""
    released: str = ""
    # Per-tune discovered immediates.
    init_ad: int = 0
    init_sr: int = 0
    gate_ctrl: int = 0
    gateoff_ctrl: int = 0
    # Per-tune discovered (relocated) table bases (absolute addresses).
    subptr_lo: int = 0
    subptr_hi: int = 0
    orderlist_ptr_table: int = constants.DEFAULT_LOAD + constants.ORDERLIST_PTR_TABLE
    # Split note-frequency tables.
    freq_lo: List[int] = field(default_factory=list)
    freq_hi: List[int] = field(default_factory=list)
    # Raw whole-image bytes (player + data) the player reads opcode streams
    # and pointer tables out of, via the discovered addresses.
    image: bytes = b""
    # The original container header (PSID/RSID bytes before the image), kept
    # for reference; empty for a bare ``.prg``.
    container_header: bytes = b""

    def orderlist_ptr(self, subtune: int, voice: int) -> int:
        """Absolute address of ``voice``'s orderlist for ``subtune``.

        The init routine indexes the pointer table by ``subtune * 8`` and
        reads three consecutive 2-byte pairs (one per voice).
        """
        base = self.orderlist_ptr_table + (subtune << 3) + voice * 2
        lo = self._byte(base)
        hi = self._byte(base + 1)
        return lo | (hi << 8)

    def subpattern_ptr(self, index: int) -> int:
        """Absolute address of subpattern ``index`` (0-127)."""
        lo = self._byte(self.subptr_lo + index)
        hi = self._byte(self.subptr_hi + index)
        return lo | (hi << 8)

    def _byte(self, abs_addr: int) -> int:
        idx = abs_addr - self.load_addr
        if 0 <= idx < len(self.image):
            return self.image[idx]
        return 0
