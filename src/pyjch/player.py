"""The JCH NewPlayer playroutine, in Python, as one player over its versions.

The JCH NewPlayer shipped in several driver versions.  Every recovered version
plays byte-exactly (validated frame-for-frame against the sidtrace register
oracle -- see ``tests/test_oracle_hvsc.py``).  Two of them run native
pure-Python engines transcribed here; every other recovered version plays via
:class:`~pysidtracker.EmuPlayer`, which runs the tune's own 6502 driver on
py65.  The two native engines are:

* **V0x** -- the canonical ``JCH_NewPlayer_V0x`` layout (init ``FUN_1060`` /
  play ``FUN_10e8``): per-voice opcode streams walked through the zero-page
  pointer ``$fb/$fc``, gate-delay counters, split note-frequency tables.  Driven
  from a :class:`~pyjch.model.Song`.
* **V20** -- the later two-column wavetable engine: per-voice ``X=2,1,0`` loop
  over parallel note/CTRL wavetable columns, a groove tempo divider, PW
  ping-pong, portamento, vibrato and a global filter sweep.  Driven from a
  :class:`~pyjch.newplayer.NewPlayerModel` whose V20 data-table bases resolve
  (see :func:`playable`).

Both are play-routine *variations* of one player, so a single
:class:`~pysidtracker.MemPlayer` subclass -- :class:`JchPlayer` -- hosts them:
the version is selected at construction from the model type, and each version's
init/frame logic lives in private ``*_v0x`` / ``*_v20`` methods.  All the shared
machinery (64 KiB memory mount, ``_rd``/``_wr``, post-init snapshot, per-frame
diffing, ``iter_frames``/``render_grid``) comes from :class:`MemPlayer`.

Both run a full 64 KiB memory image exactly as the 6502 player does, reading
work-RAM and pointer tables out of the loaded image and writing the SID
registers into ``$D400..``.  The per-tune ADDRESSES are discovered (the reader
for V0x, :func:`discover_bases` for V20), so a relocated tune plays unchanged.
"""

from dataclasses import dataclass
from typing import List, Optional

from pysidtracker import SID_BASE, EmuPlayer, MemPlayer, find_code_first

from pyjch import constants
from pyjch.errors import SidParseError
from pyjch.model import Song
from pyjch.newplayer import NewPlayerModel

_D400 = SID_BASE

# ----------------------------------------------------------------------------
# V20 work-cell / global offsets relative to the code base (cb = init address).
# Fixed by the V20 player binary; the discovery idioms below embed several of
# these operands, so a successful discovery confirms the layout.
# ----------------------------------------------------------------------------
ACTIVE = 0x006
VOL = 0x009
RESFILT = 0x00A
SCRATCH = 0x00B
FLO = 0x00C
FHI = 0x00F
NOTE = 0x014
OCT = 0x017
GATEMASK = 0x01A
INST = 0x01D
IDFLAG = 0x020
VIBDELTALO = 0x72D
ORDLO = 0x72E
ORDHI = 0x731
RSTLO = 0x734
RSTHI = 0x737
ROUTESET = 0x73A
ROUTECLR = 0x73D
STRIDE = 0x740
FINE = 0x743
GRVCTR = 0x746
TEMPREL = 0x747
NDELAY = 0x748
VIBEN = 0x74B
PORTAEN = 0x74E
PATCUR = 0x751
INSTFLAG = 0x754
NDURREL = 0x757
NDUR = 0x75A
CTRLSH = 0x75D
PORTAPEND = 0x760
VIBEN2 = 0x763
VIBSUP = 0x766
VIBDWCTR = 0x769
VIBDWREL = 0x76C
VIBBSTEP = 0x76F
VIBBACC = 0x772
VIBDIR = 0x775
VIBACCLO = 0x778
VIBACCHI = 0x77B
VIBDEP = 0x77E
PWCUR = 0x781
PWDW = 0x784
PWACCLO = 0x787
PWACCHI = 0x78A
PWDIR = 0x78D
FLTCUR = 0x790
FLTDW = 0x791
FLTCUT = 0x792
FLTMODE = 0x793
GRVIDX = 0x794
WTPTR = 0x795
WTDW = 0x798
WTDWREL = 0x79B
PORTASLO = 0x79E
PORTASHI = 0x7A1
PORTADIR = 0x7A4
PORTAACCLO = 0x7A7
PORTAACCHI = 0x7AA
STGTR = 0x7AD
STGINST = 0x7B0
STGNOTE = 0x7B3
STGPEN = 0x7B6
STGVEN = 0x7B9
STGGATE = 0x7BC
ADSH = 0x7BF
SRSH = 0x7C2
ADOVR = 0x7C5
DELAYEN = 0x7CA


def _op(image: bytes, spec: str) -> Optional[int]:
    """First ``{op}`` operand captured by ``spec`` in ``image``, or ``None``.

    ``spec`` is a :class:`~pysidtracker.CodePattern` string; ``{op:w}`` captures
    the relocated table base baked into the instruction as a 16-bit operand.
    """
    match = find_code_first(image, spec)
    return None if match is None else match.captures["op"]


@dataclass
class V20Bases:
    """Discovered per-tune table bases + hard-restart immediate for a V20 tune."""

    # pylint: disable=too-many-instance-attributes  # one field per table
    pitch: int
    subtune: int
    wave_note: int
    wave_ctrl: int
    filterprog: int
    pwprog: int
    instruments: int
    patternptr_lo: int
    patternptr_hi: int
    cmdparam: int
    rearm_ad: int
    rearm_sr: int
    hardrestart: int


def discover_bases(load: int, image: bytes, model) -> Optional[V20Bases]:
    """Resolve every V20 data-table base + hard-restart immediate, or ``None``.

    ``model`` supplies the bases already discovered by :mod:`pyjch.newplayer`
    (subtune/pattern/instrument/wave-note/pitch); this adds the wave-ctrl,
    filter-program, PW-program and command-parameter tables and the hard-restart
    CTRL byte.  Returns ``None`` if any required idiom is absent -- the signal
    the tune is not the V20 code build.
    """
    if model.pitch_table is None or model.wave_note_col is None:
        return None
    wave_ctrl = _op(image, "B9 {op:w} 9D 5D 17")  # LDA col,Y ; STA $175D,X
    filterprog = _op(image, "B9 {op:w} 8D 46 17")  # LDA prog,Y ; STA $1746
    pwnext = _op(image, "BC 81 17 B9 {op:w} 9D 81 17")  # PWProg_next
    cmdparam = _op(image, "0A A8 B9 {op:w} 48")  # ASL;TAY;LDA param,Y;PHA
    # The gate-off AD/SR re-arm reads two *fixed absolute* cells (LDA abs ; STA
    # $D405/$D406,Y) that only coincide with cmdparam in the reference build.
    rearm_ad = _op(image, "AD {op:w} 99 05 D4")
    rearm_sr = _op(image, "AD {op:w} 99 06 D4")
    if None in (wave_ctrl, filterprog, pwnext, cmdparam, rearm_ad, rearm_sr):
        return None
    # Instrument-record field offsets must match the reference build (AD+0,
    # SR+1, wave-flags+2, filter+3, filter-prog+4, PW-start+5); some V20-family
    # sub-builds pack instrument records differently -- reject those.
    wave_field = _op(image, "B9 {op:w} 48 29 80")  # LDA inst+2,Y ; PHA ; AND #$80
    filt_field = _op(image, "B9 {op:w} 48 29 F0")  # LDA inst+3,Y ; PHA ; AND #$F0
    fprog_field = _op(image, "B9 {op:w} 8D 90 17")  # LDA inst+4,Y ; STA $1790
    instr = model.instruments
    if wave_field != instr + 2 or filt_field != instr + 3 or fprog_field != instr + 4:
        return None
    # The note-path and absolute-path wave-note reads (LDY $1795,X ; LDA col,Y)
    # must index the same column; a rare sub-build splits them -- reject it.
    note_wave = _op(image, "BC 95 17 B9 {op:w} 30")  # freq_note wave read
    if note_wave != model.wave_note_col:
        return None
    # Hard-restart CTRL immediate: LDA #imm ; STA $D404,Y.
    hr = find_code_first(image, "A9 {op} 99 04 D4")
    hard = 0x09 if hr is None else hr.captures["op"]
    end = load + len(image)
    bases = V20Bases(
        pitch=model.pitch_table,
        subtune=model.subtune_table,
        wave_note=model.wave_note_col,
        wave_ctrl=wave_ctrl,
        filterprog=filterprog,
        pwprog=pwnext - 3,
        instruments=model.instruments,
        patternptr_lo=model.patternptr_lo,
        patternptr_hi=model.patternptr_hi,
        cmdparam=cmdparam,
        rearm_ad=rearm_ad,
        rearm_sr=rearm_sr,
        hardrestart=hard,
    )
    for addr in (
        bases.pitch,
        bases.subtune,
        bases.wave_note,
        bases.wave_ctrl,
        bases.filterprog,
        bases.pwprog,
        bases.instruments,
        bases.patternptr_lo,
        bases.patternptr_hi,
        bases.cmdparam,
    ):
        if not load <= addr < end:
            return None
    return bases


def playable(model) -> Optional[V20Bases]:
    """Return ``V20Bases`` if ``model`` is a byte-exact-replayable V20 tune.

    Requires the code base (init) to be a ``JMP`` vector and every V20 idiom to
    resolve in range.  This gates only the NATIVE V20 engine; ``None`` for any
    other NewPlayer family build, which :class:`JchPlayer` then plays
    byte-exactly via :class:`~pysidtracker.EmuPlayer` (the tune's own 6502
    driver on py65) rather than a native transcription.
    """
    image = model.image
    init = model.init_addr
    off = init - model.load_addr
    if not 0 <= off < len(image) - 2 or image[off] != 0x4C:
        return None
    return discover_bases(model.load_addr, image, model)


class JchPlayer(MemPlayer):
    """Play a JCH NewPlayer tune one frame at a time (V0x or V20).

    ``model`` is a :class:`~pyjch.model.Song` (drives the native pure-Python
    **V0x** routine) or a :class:`~pyjch.newplayer.NewPlayerModel` (drives the
    native **V20** engine when :func:`playable`).  Any other recovered family
    build is played byte-exactly by running the tune's own 6502 driver on py65
    via :class:`~pysidtracker.EmuPlayer` -- so every recovered JCH NewPlayer tune
    plays register-exact, whether or not its version has a native transcription.
    :meth:`play_frame` runs one tick, returning the SID register writes;
    :meth:`render_grid` (from :class:`MemPlayer`) yields the forward-filled
    25-register per-frame grid.
    """

    # pylint: disable=too-many-instance-attributes  # full player machine state
    def __init__(self, model, subtune: int = 0):
        if isinstance(model, Song):
            self._version = "v0x"
            self._init_ad = model.init_ad
            self._init_sr = model.init_sr
            self._gate_ctrl = model.gate_ctrl
            self._gateoff_ctrl = model.gateoff_ctrl
            self._subptr_lo = model.subptr_lo
            self._subptr_hi = model.subptr_hi
            image, load = model.image, model.load_addr
        elif isinstance(model, NewPlayerModel):
            bases = playable(model)
            if bases is not None:
                self._version = "v20"
                self.b = bases
                self.cb = model.init_addr
            else:
                # Any other recovered family build: play it byte-exactly by
                # running the tune's own 6502 driver on py65 (EmuPlayer), rather
                # than transcribing each version's playroutine by hand.
                self._version = "emu"
                self._emu_init = model.init_addr
                self._emu_play = model.play_addr
            image, load = model.image, model.load_addr
        else:
            raise SidParseError(f"cannot play a {type(model).__name__}")
        self._emu_image = image
        super().__init__(image, load, subtune)

    def _init(self, subtune: int) -> None:
        if self._version == "v0x":
            self._init_v0x(subtune)
        elif self._version == "v20":
            self._init_v20(subtune)
        else:
            self._emu = EmuPlayer(
                self._emu_image, self._load, self._emu_init, self._emu_play, subtune
            )

    def _frame(self) -> None:
        if self._version == "v0x":
            self._frame_v0x()
        elif self._version == "v20":
            self._frame_v20()
        else:
            self._emu._frame()

    def snapshot(self) -> List[int]:
        if self._version == "emu":
            return self._emu.snapshot()
        return super().snapshot()

    # === V0x: FUN_1060 (init) / FUN_10e8 (play) =========================
    def _init_v0x(self, subtune: int = 0) -> None:
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

    def _frame_v0x(self) -> None:
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

    # === V20: two-column wavetable engine ===============================
    def _ind(self, y: int) -> int:
        ptr = self._mem[0xFB] | (self._mem[0xFC] << 8)
        return self._mem[(ptr + y) & 0xFFFF]

    def _init_v20(self, subtune: int) -> None:
        cb, m, b = self.cb, self._mem, self.b
        y = (subtune << 3) & 0xFF
        for x in range(3):
            v = m[b.subtune + y]
            m[cb + ORDLO + x] = v
            m[cb + RSTLO + x] = v
            v = m[b.subtune + y + 1]
            m[cb + ORDHI + x] = v
            m[cb + RSTHI + x] = v
            y = (y + 2) & 0xFF
        m[cb + TEMPREL] = m[b.subtune + y]
        if m[cb + IDFLAG] != 0:
            for x in (2, 1, 0):
                v = m[b.subtune + y + 1]
                m[cb + SCRATCH] = v
                m[cb + ACTIVE + x] = v & m[cb + ROUTESET + x]
            if m[cb + SCRATCH] & 0x80:
                for x in range(3):
                    m[cb + RSTLO + x] = m[b.subtune + y + 2]
                    m[cb + RSTHI + x] = m[b.subtune + y + 3]
                    y = (y + 2) & 0xFF
        for i in range(0x17):
            m[_D400 + i] = 0
        for i in range(0x0C):
            m[cb + 0x14 + i] = 0
        for i in range(0x15):
            m[cb + 0x748 + i] = 0
        m[cb + GRVIDX] = 1
        m[cb + GRVCTR] = 3
        m[cb + VOL] = 0x0F

    def _frame_v20(self) -> None:
        cb, m = self.cb, self._mem
        g = (m[cb + GRVCTR] - 1) & 0xFF
        m[cb + GRVCTR] = g
        if g & 0x80:
            a = m[cb + TEMPREL]
            m[cb + GRVCTR] = a
            if a < 2:
                yv = m[cb + GRVIDX]
                m[cb + GRVCTR] = m[self.b.filterprog + yv]
                gi = (m[cb + GRVIDX] - 1) & 0xFF
                m[cb + GRVIDX] = gi
                if gi & 0x80:
                    m[cb + GRVIDX] = 1
        for x in (2, 1, 0):
            if m[cb + ACTIVE + x] == 0:
                continue
            a = m[cb + GRVCTR]
            if a == 0:
                v = (m[cb + NDUR + x] - 1) & 0xFF
                m[cb + NDUR + x] = v
                if v & 0x80:
                    self._commit(x)
                else:
                    self._engine(x)
            elif a == 2:
                if m[cb + NDUR + x] == 0:
                    self._fetch(x)
                else:
                    self._engine(x)
            else:
                self._engine(x)

    # -- note fetch (order/pattern sequencer) ----------------------------
    def _fetch(self, x: int) -> None:
        cb, m, b = self.cb, self._mem, self.b
        m[0xFB] = m[cb + ORDLO + x]
        m[0xFC] = m[cb + ORDHI + x]
        m[cb + NDELAY + x] = 0
        a = self._ind(0)
        if a & 0x80:  # transpose prefix
            m[cb + STGTR + x] = (a << 1) & 0xFF
            lo = (m[cb + ORDLO + x] + 1) & 0xFF
            if lo == 0:
                m[cb + ORDHI + x] = (m[cb + ORDHI + x] + 1) & 0xFF
            m[cb + ORDLO + x] = lo
            a = self._ind(1)
        m[0xFB] = m[b.patternptr_lo + a]
        m[0xFC] = m[b.patternptr_hi + a]
        while True:
            byte = self._ind(m[cb + PATCUR + x])
            if byte & 0x80:
                self._command(x, byte)
                continue
            self._note(x, byte)
            return

    def _command(self, x: int, a: int) -> None:
        cb, m, b = self.cb, self._mem, self.b
        hi = a & 0xE0
        if hi == 0x80:
            m[cb + NDELAY + x] = a & 0x10
            m[cb + NDURREL + x] = a & 0x0F
        elif hi == 0xA0:
            staged = (a << 3) & 0xFF
            m[cb + STGINST + x] = staged
            m[cb + ADOVR + x] = m[b.instruments + 1 + staged]
        else:
            yidx = ((a & 0x3F) << 1) & 0xFF
            paramlo = m[b.cmdparam + yidx]
            m[cb + SCRATCH] = paramlo & 0x0F
            phi = paramlo & 0xF0
            paramhi = m[b.cmdparam + 1 + yidx]
            if phi < 0x30:  # portamento
                m[cb + PORTADIR + x] = phi & 0x20
                m[cb + PORTASHI + x] = m[cb + SCRATCH]
                m[cb + PORTASLO + x] = paramhi
                m[cb + STGPEN + x] = 1
                m[cb + PORTAPEND + x] = 1
            elif phi == 0x60:  # vibrato
                m[cb + STGVEN + x] = 1
                m[cb + VIBEN2 + x] = 1
                m[cb + VIBBSTEP + x] = m[cb + SCRATCH]
                sp = paramhi >> 4
                m[cb + VIBDWREL + x] = sp
                m[cb + VIBDWCTR + x] = (sp - 1) & 0xFF
                m[cb + VIBDIR + x] = 0
                m[cb + VIBBACC + x] = 0
                m[cb + VIBACCLO + x] = 0
                m[cb + VIBACCHI + x] = 0
                m[cb + VIBDEP + x] = paramhi & 0x0F
            elif phi == 0xE0:  # tempo
                m[cb + TEMPREL] = paramhi
            elif phi == 0xF0:  # volume / filter-mode base
                m[cb + VOL] = paramhi
            elif phi == 0x90:  # AD override
                m[cb + ADOVR + x] = paramhi
            else:  # patch instrument wavetable start
                yy = ((paramlo & 0x1F) << 3) & 0xFF
                m[b.instruments + 6 + yy] = paramhi
                m[b.instruments + 7 + yy] = paramhi
        m[cb + PATCUR + x] = (m[cb + PATCUR + x] + 1) & 0xFF

    def _note(self, x: int, b: int) -> None:
        cb, m = self.cb, self._mem
        base = self.b
        if b == 0:  # rest / tie
            m[cb + NDELAY + x] = (m[cb + NDELAY + x] + 1) & 0xFF
            if m[cb + GATEMASK + x] != 0xFE:
                m[cb + STGGATE + x] = 0xFE
                inst = m[cb + INST + x]
                ws2 = m[base.instruments + 7 + inst]
                if ws2 != m[base.instruments + 6 + inst]:
                    m[cb + WTPTR + x] = ws2
            self._after(x)
            return
        if b == 0x7E:  # hold gate
            m[cb + NDELAY + x] = (m[cb + NDELAY + x] + 1) & 0xFF
            m[cb + STGGATE + x] = 0xFF
            self._after(x)
            return
        m[cb + STGNOTE + x] = b
        if m[cb + PORTAPEND + x] == 0:
            m[cb + STGPEN + x] = 0
        if m[cb + VIBEN2 + x] == 0:
            m[cb + STGVEN + x] = 0
        m[cb + STGGATE + x] = 0xFF
        self._after(x)

    def _after(self, x: int) -> None:
        cb, m = self.cb, self._mem
        m[cb + PATCUR + x] = (m[cb + PATCUR + x] + 1) & 0xFF
        nb = self._ind(m[cb + PATCUR + x])
        if nb == 0x7F:  # end of pattern
            m[cb + PATCUR + x] = 0
            carry = 1 if m[cb + ORDLO + x] + 1 > 0xFF else 0
            lo = (m[cb + ORDLO + x] + 1) & 0xFF
            m[cb + ORDLO + x] = lo
            m[0xFB] = lo
            hival = (m[cb + ORDHI + x] + carry) & 0xFF
            m[cb + ORDHI + x] = hival
            m[0xFC] = hival
            nxt = self._ind(0)
            aval = nxt
            if nxt == 0xFF:  # loop -> reload restart copy
                m[cb + ORDLO + x] = m[cb + RSTLO + x]
                m[cb + ORDHI + x] = m[cb + RSTHI + x]
                aval = m[cb + RSTHI + x]
            if aval == 0xFE:  # stop voice
                m[cb + ACTIVE + x] = 0
                self._wr(_D400 + 4 + m[cb + STRIDE + x], 0)
                return
        self._not_endpat(x)

    def _not_endpat(self, x: int) -> None:
        cb, m, b = self.cb, self._mem, self.b
        if m[cb + NDELAY + x] != 0:
            self._delayed(x)
            return
        m[cb + GATEMASK + x] = 0xFE
        if m[cb + INSTFLAG + x] != 0:
            y = m[cb + STRIDE + x]
            v = m[b.rearm_ad]
            self._wr(_D400 + 5 + y, v)
            m[cb + ADSH + x] = v
            v2 = m[b.rearm_sr]
            self._wr(_D400 + 6 + y, v2)
            m[cb + SRSH + x] = v2
            self._blit(x)
            return
        self._delayed(x)

    def _delayed(self, x: int) -> None:
        cb, m = self.cb, self._mem
        if m[cb + DELAYEN] != 0:
            m[cb + VIBSUP + x] = 1
            self._freq(x)
        else:
            self._engine(x)

    # -- note commit + instrument init -----------------------------------
    def _commit(self, x: int) -> None:
        cb, m = self.cb, self._mem
        m[cb + GATEMASK + x] = m[cb + STGGATE + x]
        m[cb + NOTE + x] = m[cb + STGNOTE + x]
        m[cb + OCT + x] = m[cb + STGTR + x]
        m[cb + VIBEN + x] = m[cb + STGVEN + x]
        m[cb + INST + x] = m[cb + STGINST + x]
        pe = m[cb + STGPEN + x]
        m[cb + PORTAEN + x] = pe
        if pe == 0:
            m[cb + PORTAACCLO + x] = 0
            m[cb + PORTAACCHI + x] = 0
        m[cb + NDUR + x] = m[cb + NDURREL + x]
        if m[cb + NDELAY + x] != 0:
            self._engine(x)
        else:
            self._inst_init(x)

    def _inst_init(self, x: int) -> None:
        # pylint: disable=too-many-statements
        cb, m, b = self.cb, self._mem, self.b
        y = m[cb + INST + x]
        m[cb + WTPTR + x] = m[b.instruments + 6 + y]
        wf = m[b.instruments + 2 + y]
        m[cb + INSTFLAG + x] = wf & 0x80
        m[cb + WTDW + x] = wf & 0x0F
        m[cb + WTDWREL + x] = wf & 0x0F
        pwstart = m[b.instruments + 5 + y]
        m[cb + PWCUR + x] = pwstart
        pr = m[b.pwprog + pwstart]
        if pr != 0xFF:
            m[cb + PWACCLO + x] = pr & 0xF0
            m[cb + PWACCHI + x] = pr & 0x0F
        dr = m[b.pwprog + 2 + pwstart]
        m[cb + PWDIR + x] = dr & 0x80
        m[cb + PWDW + x] = dr & 0x7F
        fm = m[b.instruments + 3 + y]
        m[cb + SCRATCH] = fm & 0xF0
        route = fm & 0x0F
        yflag = 0
        if route == 0:
            a = m[cb + RESFILT] & m[cb + ROUTECLR + x]
        elif route == 0x08:
            yflag = 1
            a = m[cb + RESFILT] & m[cb + ROUTECLR + x]
        else:
            mode = (route << 4) & 0xFF
            m[cb + FLTMODE] = mode
            self._wr(_D400 + 0x18, mode | m[cb + VOL])
            yflag = 1
            a = (m[cb + RESFILT] & 0x0F) | m[cb + ROUTESET + x] | m[cb + SCRATCH]
            if a == 0:
                yflag = 2
                a = m[cb + RESFILT] & m[cb + ROUTECLR + x]
        self._wr(_D400 + 0x17, a)
        m[cb + RESFILT] = a
        if yflag == 1:
            fp = m[b.instruments + 4 + y]
            m[cb + FLTCUR] = fp
            rv = m[b.filterprog + fp]
            if rv != 0xFF:
                m[cb + FLTCUT] = rv
            m[cb + FLTDW] = m[b.filterprog + 2 + fp]
        y = m[cb + INST + x]
        ad = m[b.instruments + 0 + y]
        strd = m[cb + STRIDE + x]
        self._wr(_D400 + 5 + strd, ad)
        m[cb + ADSH + x] = ad
        sr = m[b.instruments + 1 + y]
        if sr != m[cb + ADOVR + x]:
            sr = m[cb + ADOVR + x]
        self._wr(_D400 + 6 + strd, sr)
        m[cb + SRSH + x] = sr
        self._wr(_D400 + 4 + strd, b.hardrestart)

    # -- per-frame engine ------------------------------------------------
    def _engine(self, x: int) -> None:
        cb, m, b = self.cb, self._mem, self.b
        v = (m[cb + PWDW + x] - 1) & 0xFF
        m[cb + PWDW + x] = v
        if v & 0x80:
            y = m[cb + PWCUR + x]
            nxt = m[b.pwprog + 3 + y]
            m[cb + PWCUR + x] = nxt
            dr = m[b.pwprog + 2 + nxt]
            m[cb + PWDIR + x] = dr & 0x80
            m[cb + PWDW + x] = dr & 0x7F
            rs = m[b.pwprog + nxt]
            if rs != 0xFF:
                m[cb + PWACCLO + x] = rs & 0xF0
                m[cb + PWACCHI + x] = rs & 0x0F
        y = m[cb + PWCUR + x]
        step = m[b.pwprog + 1 + y]
        if m[cb + PWDIR + x] == 0:
            lo = m[cb + PWACCLO + x] + step
            m[cb + PWACCLO + x] = lo & 0xFF
            m[cb + PWACCHI + x] = (m[cb + PWACCHI + x] + (1 if lo > 0xFF else 0)) & 0xFF
        else:
            lo = m[cb + PWACCLO + x] - step
            m[cb + PWACCLO + x] = lo & 0xFF
            m[cb + PWACCHI + x] = (m[cb + PWACCHI + x] - (1 if lo < 0 else 0)) & 0xFF
        if x == m[b.filterprog + 3]:
            self._filt_sweep()
        self._freq(x)

    def _filt_sweep(self) -> None:
        cb, m, b = self.cb, self._mem, self.b
        v = (m[cb + FLTDW] - 1) & 0xFF
        m[cb + FLTDW] = v
        if v & 0x80:
            y = m[cb + FLTCUR]
            nxt = m[b.filterprog + 3 + y]
            m[cb + FLTCUR] = nxt
            m[cb + FLTDW] = m[b.filterprog + 2 + nxt]
            rv = m[b.filterprog + nxt]
            if rv != 0xFF:
                m[cb + FLTCUT] = rv
        y = m[cb + FLTCUR]
        m[cb + FLTCUT] = (m[cb + FLTCUT] + m[b.filterprog + 1 + y]) & 0xFF

    def _freq(self, x: int) -> None:
        cb, m, b = self.cb, self._mem, self.b
        y = m[cb + INST + x]
        if m[b.instruments + 2 + y] & 0x40:  # absolute-freq mode
            yy = m[cb + WTPTR + x]
            nb = m[b.wave_note + yy]
            if nb == 0x7E:
                m[cb + WTPTR + x] = (m[cb + WTPTR + x] - 1) & 0xFF
                yy = (yy - 1) & 0xFF
            elif nb == 0x7F:
                jt = m[b.wave_ctrl + yy]
                m[cb + WTPTR + x] = jt
                yy = jt
            m[cb + FHI + x] = m[b.wave_note + yy]
            m[cb + FLO + x] = 0
        else:
            self._freq_note(x)
        self._ctrl(x)

    def _freq_note(self, x: int) -> None:
        cb, m, b = self.cb, self._mem, self.b
        yy = m[cb + WTPTR + x]
        nb = m[b.wave_note + yy]
        hinote = False
        if nb & 0x80:
            a = nb
            hinote = True
        elif nb == 0x7E:
            m[cb + WTPTR + x] = (m[cb + WTPTR + x] - 1) & 0xFF
            yy = (yy - 1) & 0xFF
            a = m[b.wave_note + yy]
            hinote = bool(a & 0x80)
        elif nb == 0x7F:
            jt = m[b.wave_ctrl + yy]
            m[cb + WTPTR + x] = jt
            yy = jt
            a = m[b.wave_note + yy]
            hinote = bool(a & 0x80)
        else:
            a = nb
        if hinote:
            idx = (a << 1) & 0xFF
            m[cb + SCRATCH] = 1
        else:
            a = (a + m[cb + NOTE + x]) & 0xFF
            a = (a << 1) & 0xFF
            a = (a + m[cb + OCT + x]) & 0xFF
            idx = a
            m[cb + SCRATCH] = 0
        lo = m[b.pitch + idx] + m[cb + FINE + x]
        m[cb + FLO + x] = lo & 0xFF
        m[cb + FHI + x] = (m[b.pitch + 1 + idx] + (1 if lo > 0xFF else 0)) & 0xFF

    def _ctrl(self, x: int) -> None:
        cb, m, b = self.cb, self._mem, self.b
        m[cb + CTRLSH + x] = m[b.wave_ctrl + m[cb + WTPTR + x]]
        v = (m[cb + WTDW + x] - 1) & 0xFF
        m[cb + WTDW + x] = v
        if v & 0x80:
            m[cb + WTDW + x] = m[cb + WTDWREL + x]
            m[cb + WTPTR + x] = (m[cb + WTPTR + x] + 1) & 0xFF
        self._porta(x)

    def _porta(self, x: int) -> None:
        cb, m = self.cb, self._mem
        if m[cb + PORTAEN + x] == 0:
            self._vibrato(x)
            return
        if m[cb + PORTADIR + x] != 0:
            lo = m[cb + PORTAACCLO + x] - m[cb + PORTASLO + x]
            m[cb + PORTAACCLO + x] = lo & 0xFF
            hi = m[cb + PORTAACCHI + x] - m[cb + PORTASHI + x] - (1 if lo < 0 else 0)
            m[cb + PORTAACCHI + x] = hi & 0xFF
        else:
            lo = m[cb + PORTAACCLO + x] + m[cb + PORTASLO + x]
            m[cb + PORTAACCLO + x] = lo & 0xFF
            hi = m[cb + PORTAACCHI + x] + m[cb + PORTASHI + x] + (1 if lo > 0xFF else 0)
            m[cb + PORTAACCHI + x] = hi & 0xFF
        if m[cb + SCRATCH] == 0:
            lo = m[cb + FLO + x] + m[cb + PORTAACCLO + x]
            m[cb + FLO + x] = lo & 0xFF
            m[cb + FHI + x] = (
                m[cb + FHI + x] + m[cb + PORTAACCHI + x] + (1 if lo > 0xFF else 0)
            ) & 0xFF
        self._blit(x)

    def _vibrato(self, x: int) -> None:
        cb, m, b = self.cb, self._mem, self.b
        if m[cb + VIBSUP + x] != 0 or m[cb + VIBEN + x] == 0:
            self._blit(x)
            return
        n = m[cb + NOTE + x]
        idx = (n << 1) & 0xFF
        cur = m[b.pitch + idx] | (m[b.pitch + 1 + idx] << 8)
        nxt = m[b.pitch + 2 + idx] | (m[b.pitch + 3 + idx] << 8)
        d = (nxt - cur) & 0xFFFF
        m[cb + VIBDELTALO] = d & 0xFF
        m[cb + SCRATCH] = ((d >> 8) + m[cb + VIBBACC + x]) & 0xFF
        for _ in range(m[cb + VIBDEP + x]):
            val = ((m[cb + SCRATCH] << 8) | m[cb + VIBDELTALO]) >> 1
            m[cb + SCRATCH] = (val >> 8) & 0xFF
            m[cb + VIBDELTALO] = val & 0xFF
        v = (m[cb + VIBDWCTR + x] - 1) & 0xFF
        m[cb + VIBDWCTR + x] = v
        if v & 0x80:
            m[cb + VIBDIR + x] ^= 1
            m[cb + VIBDWCTR + x] = m[cb + VIBDWREL + x]
        if m[cb + VIBDIR + x] != 0:
            lo = m[cb + VIBACCLO + x] - m[cb + VIBDELTALO]
            m[cb + VIBACCLO + x] = lo & 0xFF
            hi = m[cb + VIBACCHI + x] - m[cb + SCRATCH] - (1 if lo < 0 else 0)
            m[cb + VIBACCHI + x] = hi & 0xFF
        else:
            lo = m[cb + VIBACCLO + x] + m[cb + VIBDELTALO]
            m[cb + VIBACCLO + x] = lo & 0xFF
            hi = m[cb + VIBACCHI + x] + m[cb + SCRATCH] + (1 if lo > 0xFF else 0)
            m[cb + VIBACCHI + x] = hi & 0xFF
        lo = m[cb + FLO + x] + m[cb + VIBACCLO + x]
        m[cb + FLO + x] = lo & 0xFF
        m[cb + FHI + x] = (
            m[cb + FHI + x] + m[cb + VIBACCHI + x] + (1 if lo > 0xFF else 0)
        ) & 0xFF
        m[cb + VIBBACC + x] = (m[cb + VIBBACC + x] + m[cb + VIBBSTEP + x]) & 0xFF
        self._blit(x)

    def _blit(self, x: int) -> None:
        cb, m = self.cb, self._mem
        m[cb + PORTAPEND + x] = 0
        m[cb + VIBEN2 + x] = 0
        m[cb + VIBSUP + x] = 0
        y = m[cb + STRIDE + x]
        self._wr(_D400 + 2 + y, m[cb + PWACCLO + x])
        self._wr(_D400 + 3 + y, m[cb + PWACCHI + x])
        self._wr(_D400 + 0x16, m[cb + FLTCUT])
        self._wr(_D400 + 0 + y, m[cb + FLO + x])
        self._wr(_D400 + 1 + y, m[cb + FHI + x])
        self._wr(_D400 + 5 + y, m[cb + ADSH + x])
        self._wr(_D400 + 6 + y, m[cb + SRSH + x])
        self._wr(_D400 + 4 + y, m[cb + CTRLSH + x] & m[cb + GATEMASK + x])
        self._wr(_D400 + 0x18, m[cb + FLTMODE] | m[cb + VOL])


def iter_frames(model, max_frames: Optional[int] = None, subtune: int = 0):
    """Yield per-frame ``(reg, val)`` write lists for ``model``.

    Stops after ``max_frames`` frames (required for a non-looping render, since
    the JCH player loops forever).
    """
    return JchPlayer(model, subtune).iter_frames(max_frames)


def render_grid(model, nframes: int, subtune: int = 0) -> List[List[int]]:
    """Render ``nframes`` of forward-filled per-frame register snapshots."""
    return JchPlayer(model, subtune).render_grid(nframes)
