"""The JCH NewPlayer playroutines behind :func:`make_player`.

The JCH NewPlayer shipped in several driver versions.  Every recovered version
plays byte-exactly (validated frame-for-frame against the sidtrace register
oracle -- see ``tests/test_oracle_hvsc.py``):

* **V0x** -- the canonical ``JCH_NewPlayer_V0x`` layout (init ``FUN_1060`` /
  play ``FUN_10e8``): per-voice opcode streams walked through the zero-page
  pointer ``$fb/$fc``, gate-delay counters, split note-frequency tables.  Driven
  from a :class:`~pyjch.model.Song` by :class:`JchPlayer`, a pure-Python
  transcription over a 64 KiB memory image.
* **V20** -- the later two-column wavetable engine: per-voice ``X=2,1,0`` loop
  over parallel note/CTRL wavetable columns, a groove tempo divider, PW
  ping-pong, portamento, vibrato and a global filter sweep.  Stated as the
  playspec ``specs/newplayer.play`` and run by :class:`V20Player` on the memory
  the tune's own init leaves; the spec is bound to the per-tune addresses that
  :func:`v20_bases` discovers, so a relocated tune plays unchanged.
* every other recovered version plays on :class:`~pysidtracker.EmuPlayer`,
  which runs the tune's own 6502 driver on jennings.
"""

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import List, Optional, Tuple

from pysidtracker import SID_BASE, EmuPlayer, MemPlayer, is_jmp_vector
from pysidtracker.playspec import SpecPlayer, load

from pyjch import constants
from pyjch.errors import SidParseError
from pyjch.model import Song
from pyjch.newplayer import (
    NewPlayerModel,
    abs_tokens,
    find_operand,
    hard_restart_site,
)

_D400 = SID_BASE

# ----------------------------------------------------------------------------
# V20 work-cell / global offsets relative to the code base (cb = init address).
# Fixed by the V20 player binary; the discovery idioms below embed several of
# these operands, so a successful discovery confirms the layout.
# ----------------------------------------------------------------------------
GRVCTR = 0x746
CTRLSH = 0x75D
PWCUR = 0x781
FLTCUR = 0x790
WTPTR = 0x795


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


# The V20 sub-build gate: ``reference`` (the native engine plays it) or why the
# build was rejected.
V20_REFERENCE = "reference"
V20_NOT_JMP = "entry-not-jmp"
V20_NO_TABLES = "no-pitch-or-wave"
V20_IDIOMS = "idioms-missing"
V20_INSTRUMENT_FIELDS = "instrument-fields"
V20_WAVE_SPLIT = "wave-split"
V20_OUT_OF_RANGE = "out-of-range"
V20_GATES = (
    V20_REFERENCE,
    V20_NOT_JMP,
    V20_NO_TABLES,
    V20_IDIOMS,
    V20_INSTRUMENT_FIELDS,
    V20_WAVE_SPLIT,
    V20_OUT_OF_RANGE,
)


def v20_bases(model) -> Tuple[Optional[V20Bases], str]:
    """Resolve every V20 data-table base + hard-restart immediate, with the gate.

    Returns ``(bases, "reference")`` for a V20 code build and ``(None, reason)``
    otherwise, ``reason`` naming the sub-build gate that rejected it.  ``model``
    supplies the bases already discovered by :mod:`pyjch.newplayer`
    (subtune/pattern/instrument/wave-note/pitch); this adds the wave-ctrl,
    filter-program, PW-program and command-parameter tables and the hard-restart
    CTRL byte.  The code base (init) must be a ``JMP`` vector.
    """
    img = model.sid
    if not is_jmp_vector(img, model.init_addr):
        return None, V20_NOT_JMP
    if model.pitch_table is None or model.wave_note_col is None:
        return None, V20_NO_TABLES
    # Work-cell operands sit at fixed offsets from the code base (the init
    # address), so the idioms are built per-tune and match relocated builds.
    cb = model.init_addr
    ctrlsh, grvctr, pwcur, fltcur, wtptr = (
        abs_tokens(cb + o) for o in (CTRLSH, GRVCTR, PWCUR, FLTCUR, WTPTR)
    )
    wave_ctrl = find_operand(
        img, f"B9 {{op:w}} 9D {ctrlsh}"
    )  # LDA col,Y ; STA CTRLSH,X
    filterprog = find_operand(
        img, f"B9 {{op:w}} 8D {grvctr}"
    )  # LDA prog,Y ; STA GRVCTR
    pwnext = find_operand(img, f"BC {pwcur} B9 {{op:w}} 9D {pwcur}")  # PWProg_next
    cmdparam = find_operand(img, "0A A8 B9 {op:w} 48")  # ASL;TAY;LDA param,Y;PHA
    # The gate-off AD/SR re-arm reads two *fixed absolute* cells (LDA abs ; STA
    # $D405/$D406,Y) that only coincide with cmdparam in the reference build.
    rearm_ad = find_operand(img, "AD {op:w} 99 05 D4")
    rearm_sr = find_operand(img, "AD {op:w} 99 06 D4")
    if None in (wave_ctrl, filterprog, pwnext, cmdparam, rearm_ad, rearm_sr):
        return None, V20_IDIOMS
    # Instrument-record field offsets must match the reference build (AD+0,
    # SR+1, wave-flags+2, filter+3, filter-prog+4, PW-start+5); some V20-family
    # sub-builds pack instrument records differently -- reject those.
    wave_field = find_operand(
        img, "B9 {op:w} 48 29 80"
    )  # LDA inst+2,Y ; PHA ; AND #$80
    filt_field = find_operand(
        img, "B9 {op:w} 48 29 F0"
    )  # LDA inst+3,Y ; PHA ; AND #$F0
    fprog_field = find_operand(img, f"B9 {{op:w}} 8D {fltcur}")  # inst+4,Y ; STA FLTCUR
    instr = model.instruments
    if wave_field != instr + 2 or filt_field != instr + 3 or fprog_field != instr + 4:
        return None, V20_INSTRUMENT_FIELDS
    # The note-path and absolute-path wave-note reads (LDY WTPTR,X ; LDA col,Y)
    # must index the same column; a rare sub-build splits them -- reject it.
    note_wave = find_operand(img, f"BC {wtptr} B9 {{op:w}} 30")  # freq_note wave read
    if note_wave != model.wave_note_col:
        return None, V20_WAVE_SPLIT
    # Hard-restart CTRL immediate: LDA #imm ; STA $D404,Y.
    hr = hard_restart_site(img)
    hard = 0x09 if hr is None else hr.captures["op"]
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
        if not img.contains(addr):
            return None, V20_OUT_OF_RANGE
    return bases, V20_REFERENCE


def playable(model) -> Optional[V20Bases]:
    """Return ``V20Bases`` if ``model`` is a byte-exact-replayable V20 tune.

    Requires the code base (init) to be a ``JMP`` vector and every V20 idiom to
    resolve in range (see :func:`v20_bases` for the gate that rejects a build).
    This gates only the NATIVE V20 engine; ``None`` for any other NewPlayer
    family build, which :func:`make_player` plays byte-exactly on
    :class:`~pysidtracker.EmuPlayer` (the tune's own 6502 driver on jennings)
    rather than a native transcription.
    """
    return v20_bases(model)[0]


class JchPlayer(MemPlayer):
    """Play a V0x JCH NewPlayer tune one frame at a time on a native engine.

    ``model`` is a :class:`~pyjch.model.Song` (the pure-Python **V0x** routine);
    :func:`make_player` plays a :func:`playable` V20 model on
    :class:`V20Player` and any other recovered family build on
    :class:`~pysidtracker.EmuPlayer`.  :meth:`play_frame` runs one tick,
    returning the SID register writes; :meth:`render_grid` (from
    :class:`MemPlayer`) yields the forward-filled 25-register per-frame grid,
    pulse-width highs nibble-masked as by ``EmuPlayer``.
    """

    MASK_PW_HI = True

    # pylint: disable=too-many-instance-attributes  # full player machine state
    def __init__(self, model, subtune: int = 0):
        if not isinstance(model, Song):
            raise SidParseError(
                f"JchPlayer plays a V0x Song, not a {type(model).__name__}; "
                "use make_player"
            )
        self._init_ad = model.init_ad
        self._init_sr = model.init_sr
        self._gate_ctrl = model.gate_ctrl
        self._gateoff_ctrl = model.gateoff_ctrl
        self._subptr_lo = model.subptr_lo
        self._subptr_hi = model.subptr_hi
        super().__init__(model.image, model.load_addr, subtune)

    engine = "v0x"

    def _init(self, subtune: int) -> None:
        self._init_v0x(subtune)

    def _frame(self) -> None:
        self._frame_v0x()

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


def v20_layout(model, bases: V20Bases) -> dict:
    """The binding of ``newplayer.play`` to ``model``'s discovered V20 build."""
    return {
        "cb": model.init_addr,
        "pitch": bases.pitch,
        "subtune": bases.subtune,
        "wave_note": bases.wave_note,
        "wave_ctrl": bases.wave_ctrl,
        "filterprog": bases.filterprog,
        "pwprog": bases.pwprog,
        "instruments": bases.instruments,
        "patlo": bases.patternptr_lo,
        "pathi": bases.patternptr_hi,
        "cmdparam": bases.cmdparam,
        "rearm_ad": bases.rearm_ad,
        "rearm_sr": bases.rearm_sr,
        "hardrestart": bases.hardrestart,
    }


@lru_cache(maxsize=1)
def spec():
    """The parsed ``newplayer.play`` playspec."""
    return load(Path(__file__).with_name("specs") / "newplayer.play")


class V20Player(SpecPlayer):
    """The V20 playroutine: ``newplayer.play`` run on the tune's post-init memory."""

    engine = "v20"
    MASK_PW_HI = True

    def __init__(self, model, subtune: int = 0):
        bases = playable(model)
        if bases is None:
            raise SidParseError(
                f"JCH NewPlayer {model.version} has no V20 spec; "
                "make_player plays it on EmuPlayer"
            )
        layout = v20_layout(model, bases)
        super().__init__(
            spec(),
            model.image,
            model.load_addr,
            model.init_addr,
            model.play_addr,
            subtune,
            bind=lambda view, entry: layout,
        )


def make_player(model, subtune: Optional[int] = None) -> MemPlayer:
    """The byte-exact player of ``model``'s ``subtune`` (default: its own).

    A :class:`JchPlayer` for a V0x :class:`~pyjch.model.Song`; a
    :class:`V20Player` for a :func:`playable` V20 model;
    :class:`~pysidtracker.EmuPlayer` running the tune's own driver for any
    other recovered NewPlayer family build.
    """
    if subtune is None:
        subtune = model.subtune
    if isinstance(model, NewPlayerModel):
        if playable(model) is None:
            return EmuPlayer(
                model.image, model.load_addr, model.init_addr, model.play_addr, subtune
            )
        return V20Player(model, subtune)
    return JchPlayer(model, subtune)


def engine_of(player: MemPlayer) -> str:
    """The playroutine ``player`` runs: ``v0x``/``v20``, else ``emu``."""
    return player.engine if isinstance(player, (JchPlayer, V20Player)) else "emu"


def iter_frames(model, max_frames: Optional[int] = None, subtune: Optional[int] = None):
    """Yield per-frame ``(reg, val)`` write lists for ``model``.

    Stops after ``max_frames`` frames (required for a non-looping render, since
    the JCH player loops forever).
    """
    return make_player(model, subtune).iter_frames(max_frames)


def render_grid(model, nframes: int, subtune: Optional[int] = None) -> List[List[int]]:
    """Render ``nframes`` of forward-filled per-frame register snapshots."""
    return make_player(model, subtune).render_grid(nframes)
