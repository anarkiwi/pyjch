"""The JCH NewPlayer playroutines behind :func:`make_player`.

The JCH NewPlayer shipped in several driver versions.  Every recovered version
plays byte-exactly (validated frame-for-frame against the sidtrace register
oracle -- see ``tests/test_oracle_hvsc.py``):

* **V0x** -- the canonical ``JCH_NewPlayer_V0x`` layout (init ``FUN_1060`` /
  play ``FUN_10e8``): per-voice opcode streams walked through the zero-page
  pointer ``$fb/$fc``, gate-delay counters, split note-frequency tables.  Stated
  as the playspec ``specs/v0x.play``, driven from a :class:`~pyjch.model.Song`.
* **V20** -- the later two-column wavetable engine: per-voice ``X=2,1,0`` loop
  over parallel note/CTRL wavetable columns, a groove tempo divider, PW
  ping-pong, portamento, vibrato and a global filter sweep.  Stated as the
  playspec ``specs/newplayer.play``; the spec is bound to the per-tune
  addresses that :func:`v20_bases` discovers, so a relocated tune plays
  unchanged.
* every other recovered version plays on :class:`~pysidtracker.EmuPlayer`,
  which runs the tune's own 6502 driver on jennings.

:class:`JchPlayer` runs a spec with :class:`~pysidtracker.playspec.SpecPlayer`
on the memory the tune's own init leaves.
"""

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import List, Optional, Tuple

from pysidtracker import EmuPlayer, MemPlayer, is_jmp_vector
from pysidtracker.playspec import SpecPlayer, load

from pyjch.errors import SidParseError
from pyjch.model import Song
from pyjch.newplayer import (
    NewPlayerModel,
    abs_tokens,
    find_operand,
    find_site,
    hard_restart_site,
)

# ----------------------------------------------------------------------------
# V20 work-cell / global offsets relative to the code base (cb = init address).
# Fixed by the V20 player binary; the discovery idioms below embed several of
# these operands, so a successful discovery confirms the layout.
# ----------------------------------------------------------------------------
FLTDW = 0x791
FLTCUT = 0x792
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
V20_CODE = "code-differs"
V20_GATES = (
    V20_REFERENCE,
    V20_NOT_JMP,
    V20_NO_TABLES,
    V20_IDIOMS,
    V20_INSTRUMENT_FIELDS,
    V20_WAVE_SPLIT,
    V20_OUT_OF_RANGE,
    V20_CODE,
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
    # Code the spec states but the SID registers do not show: the saved zero-page
    # pointer, and the filter-program start (cutoff kept at $FF, dwell loaded).
    fltcut, fltdw = (abs_tokens(cb + o) for o in (FLTCUT, FLTDW))
    filter_start = find_site(
        img, f"B9 {{lead:w}} C9 FF F0 03 8D {fltcut} B9 {{dwell:w}} 8D {fltdw}"
    )
    if (
        find_site(img, "A5 FB 48 A5 FC 48") is None
        or find_site(img, "68 85 FC 68 85 FB 60") is None
        or filter_start is None
        or filter_start.captures["lead"] != filterprog
        or filter_start.captures["dwell"] != filterprog + 2
    ):
        return None, V20_CODE
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


def v0x_layout(song: Song) -> dict:
    """The binding of ``v0x.play`` to a V0x ``song``."""
    return {
        "load": song.load_addr,
        "subptr_lo": song.subptr_lo,
        "subptr_hi": song.subptr_hi,
        "gate_ctrl": song.gate_ctrl,
        "gateoff_ctrl": song.gateoff_ctrl,
    }


@lru_cache(maxsize=None)
def spec(name: str):
    """The parsed playspec ``name`` (``v0x`` or ``newplayer``)."""
    return load(Path(__file__).with_name("specs") / f"{name}.play")


class JchPlayer(SpecPlayer):
    """Play a V0x or V20 JCH NewPlayer tune: its playspec on the post-init memory.

    ``model`` is a :class:`~pyjch.model.Song` (``v0x.play``) or a
    :func:`playable` :class:`~pyjch.newplayer.NewPlayerModel`
    (``newplayer.play``); :func:`make_player` plays any other recovered family
    build on :class:`~pysidtracker.EmuPlayer`.  :meth:`~pysidtracker.MemPlayer.play_frame` runs one tick,
    returning the SID register writes; :meth:`~pysidtracker.MemPlayer.render_grid` yields the
    forward-filled 25-register per-frame grid, pulse-width highs nibble-masked as
    by ``EmuPlayer``.
    """

    MASK_PW_HI = True

    def __init__(self, model, subtune: int = 0):
        if isinstance(model, Song):
            self.engine, name, layout = "v0x", "v0x", v0x_layout(model)
        elif isinstance(model, NewPlayerModel):
            bases = playable(model)
            if bases is None:
                raise SidParseError(
                    f"JCH NewPlayer {model.version} has no spec; "
                    "make_player plays it on EmuPlayer"
                )
            self.engine, name, layout = "v20", "newplayer", v20_layout(model, bases)
        else:
            raise SidParseError(f"cannot play a {type(model).__name__}")
        super().__init__(
            spec(name),
            model.image,
            model.load_addr,
            model.init_addr,
            model.play_addr,
            subtune,
            bind=lambda view, entry: layout,
        )


def make_player(model, subtune: Optional[int] = None) -> MemPlayer:
    """The byte-exact player of ``model``'s ``subtune`` (default: its own).

    A :class:`JchPlayer` for a V0x :class:`~pyjch.model.Song` or a
    :func:`playable` V20 model; :class:`~pysidtracker.EmuPlayer` running the
    tune's own driver for any other recovered NewPlayer family build.
    """
    if subtune is None:
        subtune = model.subtune
    if isinstance(model, NewPlayerModel) and playable(model) is None:
        return EmuPlayer(
            model.image, model.load_addr, model.init_addr, model.play_addr, subtune
        )
    return JchPlayer(model, subtune)


def engine_of(player: MemPlayer) -> str:
    """The playroutine ``player`` runs: ``v0x``/``v20``, else ``emu``."""
    return player.engine if isinstance(player, JchPlayer) else "emu"


def iter_frames(model, max_frames: Optional[int] = None, subtune: Optional[int] = None):
    """Yield per-frame ``(reg, val)`` write lists for ``model``.

    Stops after ``max_frames`` frames (required for a non-looping render, since
    the JCH player loops forever).
    """
    return make_player(model, subtune).iter_frames(max_frames)


def render_grid(model, nframes: int, subtune: Optional[int] = None) -> List[List[int]]:
    """Render ``nframes`` of forward-filled per-frame register snapshots."""
    return make_player(model, subtune).render_grid(nframes)
