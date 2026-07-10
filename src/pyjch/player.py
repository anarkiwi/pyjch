"""JCH NewPlayer playroutine in Python.

A faithful, byte-exact transcription of the JCH NewPlayer's init
(``FUN_1060``) and play (``FUN_10e8``) routines, recovered from the
player's disassembly (``re-trackers/JCH_NewPlayer/disasm.asm``) and
validated byte-exact against the ``preframr-sidtrace`` register oracle.

The transcription keeps full 6502 semantics: a 64K memory image (the
player walks per-voice opcode streams via the zero-page pointer
``$fb/$fc``, reads its work-RAM and pointer tables out of the loaded
image, and writes the SID registers into ``$D400..``).  The per-tune
ADDRESSES and immediates it operates on are discovered by the reader, so
a relocated tune of the same player plays unchanged.

Each :meth:`Player.play_frame` call runs one frame and returns the SID
register writes for that frame as ``(register, value)`` pairs in
ascending register order (all 25 on the first frame, only changed
registers afterwards).
"""

from typing import List, Optional

from pysidtracker import SID_BASE, MemPlayer

from pyjch import constants
from pyjch.model import Song

_D400 = SID_BASE


class Player(MemPlayer):
    """Play a :class:`~pyjch.model.Song` one frame at a time."""

    # pylint: disable=too-many-instance-attributes  # full player machine state
    def __init__(self, song: Song, subtune: int = 0):
        self._song = song
        # Per-tune discovered values (already resolved by the reader).
        self._init_ad = song.init_ad
        self._init_sr = song.init_sr
        self._gate_ctrl = song.gate_ctrl
        self._gateoff_ctrl = song.gateoff_ctrl
        self._subptr_lo = song.subptr_lo
        self._subptr_hi = song.subptr_hi
        super().__init__(song.image, song.load_addr, subtune)

    # -- init: FUN_1060(subtune) -----------------------------------------
    def _init(self, subtune: int = 0) -> None:
        m = self._mem
        load = self._load
        # Y = subtune*8; loop X=0..2 copying orderlist ptr pairs into the
        # per-voice cur/base pointers.
        y = (subtune << 3) & 0xFF
        for x in range(3):
            uvar = m[(load + constants.ORDERLIST_PTR_TABLE + y) & 0xFFFF]
            m[(load + constants.WORK_CUR_PTR_LO + x) & 0xFFFF] = uvar
            m[(load + constants.WORK_BASE_PTR_LO + x) & 0xFFFF] = uvar
            uvar = m[(load + constants.ORDERLIST_PTR_TABLE + y + 1) & 0xFFFF]
            m[(load + constants.WORK_CUR_PTR_HI + x) & 0xFFFF] = uvar
            m[(load + constants.WORK_BASE_PTR_HI + x) & 0xFFFF] = uvar
            y = (y + 2) & 0xFF
        # Tempo reload from the 7th pair byte.
        m[(load + constants.WORK_TEMPO) & 0xFFFF] = m[
            (load + constants.ORDERLIST_PTR_TABLE + y) & 0xFFFF
        ]
        m[(load + constants.WORK_TEMPO + 1) & 0xFFFF] = m[
            (load + constants.WORK_TEMPO) & 0xFFFF
        ]
        for x in range(6):
            m[(load + 0x305 + x) & 0xFFFF] = 0
        for x in range(0x19):
            m[(_D400 + x) & 0xFFFF] = 0
        m[_D400 + 0x04] = constants.INIT_CTRL
        m[_D400 + 0x0B] = constants.INIT_CTRL
        m[_D400 + 0x12] = constants.INIT_CTRL
        m[_D400 + 0x05] = self._init_ad
        m[_D400 + 0x0C] = self._init_ad
        m[_D400 + 0x13] = self._init_ad
        m[_D400 + 0x06] = self._init_sr
        m[_D400 + 0x0D] = self._init_sr
        m[_D400 + 0x14] = self._init_sr
        m[_D400 + 0x03] = constants.INIT_PW_HI_V0
        m[_D400 + 0x0A] = constants.INIT_PW_HI_V12
        m[_D400 + 0x11] = constants.INIT_PW_HI_V12
        m[_D400 + 0x18] = constants.INIT_VOLUME
        for x in range(3):
            m[(load + constants.WORK_GATE_CTR + x) & 0xFFFF] = 0xFF
            m[(load + constants.WORK_GATE_RELOAD + x) & 0xFFFF] = 0x03

    # -- play: FUN_10e8 --------------------------------------------------
    def _frame(self) -> None:
        load = self._load
        rd = self._rd
        wr = self._wr

        x = 2
        while True:
            # 10f0: load cur ptr for voice X into the zero-page pointer.
            wr(constants.ZP_PTR_LO, rd(load + constants.WORK_CUR_PTR_LO + x))
            wr(constants.ZP_PTR_HI, rd(load + constants.WORK_CUR_PTR_HI + x))
            a = rd(load + constants.WORK_GATE_CTR + x)  # gate-delay counter
            if a & 0x80:  # BMI -> LAB_110a (a < 0)
                self._note_advance(x)
            else:
                wr(load + constants.WORK_GATE_FLAG + x, 1)
            # 11d7:
            if rd(load + constants.WORK_GATE_FLAG + x) == 0:
                wr(
                    _D400 + 0x04 + rd(load + constants.WORK_SID_OFFSET + x),
                    self._gate_ctrl,
                )
            # 11e5:
            yreg = rd(load + constants.WORK_SID_OFFSET + x)
            wr(_D400 + yreg, rd(load + constants.WORK_FREQ_LO + x))
            wr(_D400 + 0x01 + yreg, rd(load + constants.WORK_FREQ_HI + x))
            wr(load + constants.WORK_CUR_PTR_LO + x, rd(constants.ZP_PTR_LO))
            wr(load + constants.WORK_CUR_PTR_HI + x, rd(constants.ZP_PTR_HI))
            x = (x - 1) & 0xFF
            if x & 0x80:  # BMI 1204
                break
        # 1204: tempo divider; on wrap, decrement the per-voice gate counters.
        tempo = (rd(load + constants.WORK_TEMPO) - 1) & 0xFF
        wr(load + constants.WORK_TEMPO, tempo)
        if tempo != 0:
            return
        wr(load + constants.WORK_TEMPO, rd(load + constants.WORK_TEMPO + 1))
        for off in range(3):
            wr(
                load + constants.WORK_GATE_CTR + off,
                (rd(load + constants.WORK_GATE_CTR + off) - 1) & 0xFF,
            )

    def _ptr_get(self) -> int:
        return self._rd(constants.ZP_PTR_LO) | (self._rd(constants.ZP_PTR_HI) << 8)

    def _inc_fb(self) -> None:
        v = (self._rd(constants.ZP_PTR_LO) + 1) & 0xFF
        self._wr(constants.ZP_PTR_LO, v)
        if v == 0:
            self._wr(constants.ZP_PTR_HI, (self._rd(constants.ZP_PTR_HI) + 1) & 0xFF)

    def _note_advance(self, x: int) -> None:
        # pylint: disable=too-many-statements,too-many-branches  # 6502 label graph
        load = self._load
        rd = self._rd
        wr = self._wr
        bvar1 = 0  # latched between labels (the 6502 accumulator)
        pc = "110a"
        while True:
            if pc == "110a":
                wr(
                    load + constants.WORK_GATE_CTR + x,
                    rd(load + constants.WORK_GATE_RELOAD + x),
                )
                pc = "1110"
            elif pc == "1110":
                pc = (
                    "1118"
                    if rd(load + constants.WORK_SUBPAT_DEPTH + x) == 0
                    else "1163"
                )
            elif pc == "1118":
                bvar1 = rd(self._ptr_get())  # LDA (fb),Y  Y=0
                if bvar1 < 0x80:  # CMP #$80; BPL 113b -> else (<$80) fallthrough
                    # 1120: subpattern-ref; push cur ptr, jump via subptr tables.
                    yidx = bvar1
                    wr(load + constants.WORK_SAVE_PTR_LO + x, rd(constants.ZP_PTR_LO))
                    wr(load + constants.WORK_SAVE_PTR_HI + x, rd(constants.ZP_PTR_HI))
                    wr(constants.ZP_PTR_LO, rd(self._subptr_lo + yidx))
                    wr(constants.ZP_PTR_HI, rd(self._subptr_hi + yidx))
                    wr(
                        load + constants.WORK_SUBPAT_DEPTH + x,
                        (rd(load + constants.WORK_SUBPAT_DEPTH + x) + 1) & 0xFF,
                    )
                    pc = "1163"
                else:  # 113b
                    if (bvar1 & 0xE0) != 0x80:  # BNE 1155
                        pc = "1155"
                    else:
                        v = (((bvar1 & 0x1F) - 0x0C) << 1) & 0xFF
                        wr(load + constants.WORK_TRANSPOSE + x, v)
                        self._inc_fb()
                        pc = "1118"
            elif pc == "1155":
                wr(constants.ZP_PTR_LO, rd(load + constants.WORK_BASE_PTR_LO + x))
                wr(constants.ZP_PTR_HI, rd(load + constants.WORK_BASE_PTR_HI + x))
                pc = "110a"
            elif pc == "1163":
                bvar1 = rd(self._ptr_get())
                if bvar1 >= 0x80:  # BPL 11a5 -> else (>=$80)
                    pc = "11a5"
                else:
                    c = (bvar1 << 1) & 0xFF  # ASL A
                    if c == 0:  # BEQ 1195
                        pc = "1195"
                    elif c == 0xFE:  # CMP #$fe; BEQ 119d
                        pc = "119d"
                    else:
                        bv2 = (
                            c
                            + rd(load + constants.VOICE_TRANSPOSE + x)
                            + rd(load + constants.WORK_TRANSPOSE + x)
                        ) & 0xFF
                        lo = rd(load + constants.FREQ_LO + bv2)
                        add = rd(load + constants.VOICE_DETUNE + x)
                        s = lo + add
                        wr(load + constants.WORK_FREQ_LO + x, s & 0xFF)
                        hi = rd(load + constants.FREQ_HI + bv2)
                        wr(
                            load + constants.WORK_FREQ_HI + x,
                            (hi + (1 if s > 0xFF else 0)) & 0xFF,
                        )
                        wr(load + constants.WORK_GATE_FLAG + x, 0)
                        pc = "11d1"
            elif pc == "1195":
                wr(
                    _D400 + 0x04 + rd(load + constants.WORK_SID_OFFSET + x),
                    self._gateoff_ctrl,
                )
                pc = "119d"
            elif pc == "119d":
                wr(load + constants.WORK_GATE_FLAG + x, 1)
                pc = "11d1"
            elif pc == "11a5":
                if (bvar1 & 0xF0) == 0x80:  # BNE 11b8 -> else (==$80)
                    val = bvar1 & 0x0F
                    wr(load + constants.WORK_GATE_CTR + x, val)
                    wr(load + constants.WORK_GATE_RELOAD + x, val)
                    pc = "11c8"
                else:
                    pc = "11b8"
            elif pc == "11b8":
                wr(load + constants.WORK_SUBPAT_DEPTH + x, 0)
                wr(constants.ZP_PTR_LO, rd(load + constants.WORK_SAVE_PTR_LO + x))
                wr(constants.ZP_PTR_HI, rd(load + constants.WORK_SAVE_PTR_HI + x))
                pc = "11c8"
            elif pc == "11c8":
                self._inc_fb()
                pc = "1110"
            elif pc == "11d1":
                self._inc_fb()
                return


def iter_frames(song: Song, max_frames: Optional[int] = None):
    """Yield per-frame register write lists for ``song``.

    Stops after ``max_frames`` frames (required for a non-looping render,
    since the JCH player loops forever).
    """
    return Player(song).iter_frames(max_frames)


def render_grid(song: Song, nframes: int) -> List[List[int]]:
    """Render ``nframes`` of forward-filled per-frame register snapshots."""
    return Player(song).render_grid(nframes)
