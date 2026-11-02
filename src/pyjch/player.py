"""The JCH NewPlayer playroutines behind :func:`make_player`.

The JCH NewPlayer shipped in several driver versions.  Every recovered version
plays byte-exactly (validated frame-for-frame against the sidtrace register
oracle -- see ``tests/test_oracle_hvsc.py``):

* **V0x** -- the canonical ``JCH_NewPlayer_V0x`` layout (init ``FUN_1060`` /
  play ``FUN_10e8``): per-voice order lists of subpatterns, beat counters, split
  note-frequency tables.  Stated as the playspec ``specs/v0x.play``, driven from a
  :class:`~pyjch.model.Song`.
* **V20** -- the later two-column wavetable engine: a groove tempo divider, a pattern
  row read two ticks ahead, parallel note/CTRL wavetable columns, pulse-width and filter
  programs, portamento, vibrato and hard restart.  Stated as the playspec
  ``specs/newplayer.play``, bound to the per-tune addresses that :func:`v20_bases`
  discovers; :func:`spec_gate` leaves to the 6502 the tunes the spec cannot place
  (:mod:`pyjch.refusal`).
* every other recovered version plays on :class:`~pysidtracker.EmuPlayer`,
  which runs the tune's own 6502 driver on jennings.

:class:`JchPlayer` runs a spec with :class:`~pysidtracker.playspec.SpecPlayer`
from the variables the tune's own init leaves.
"""

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Callable, Iterator, List, Optional, Tuple

from pysidtracker import EmuPlayer, MemPlayer, is_jmp_vector
from pysidtracker.formats import SpecBinding
from pysidtracker.playspec import SpecPlayer, load

from pyjch.constants import (
    SHARED_BYTES,
    SPEC_REFUSALS,
    V0X_BYTES,
    V0X_SHARED,
    V0X_VOICE_REGISTERS,
    V0X_WORDS,
    VARIABLE_SHIFTS,
    VOICE_BYTES,
    VOICE_WORDS,
)
from pyjch.errors import SidParseError
from pyjch.model import Song
from pyjch.newplayer import (
    NewPlayerModel,
    abs_tokens,
    find_operand,
    find_site,
    hard_restart_site,
)

# The V20 player keeps its variables in a work area at fixed offsets from the code base (the
# init address); the discovery idioms embed several of these operands, so a successful
# discovery confirms the layout.
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
    *SPEC_REFUSALS,
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
    # Code the SID registers do not show: the saved zero-page pointer, and the
    # filter-program start (cutoff kept at $FF, dwell loaded).
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


def spec_gate(model) -> Tuple[Optional[V20Bases], str]:
    """``(bases, "reference")`` when the spec plays ``model``, else ``(None, reason)``: the
    :func:`v20_bases` gate, or the :mod:`pyjch.refusal` reason the spec cannot place the tune.
    """
    if hasattr(model, "_spec_gate"):
        return model._spec_gate  # pylint: disable=protected-access
    bases, gate = v20_bases(model)
    if bases is not None:
        # pylint: disable-next=import-outside-toplevel
        from pyjch.refusal import refusal

        why = refusal(model, bases)
        bases, gate = (None, why) if why else (bases, gate)
    model._spec_gate = bases, gate  # pylint: disable=protected-access
    return bases, gate


def playable(model) -> Optional[V20Bases]:
    """Return ``V20Bases`` if the spec plays ``model`` byte-exactly (see :func:`spec_gate`).

    Any other NewPlayer family build, and a V20 build the spec cannot place, plays on
    :class:`~pysidtracker.EmuPlayer`, the tune's own 6502 driver on jennings.
    """
    return spec_gate(model)[0]


def v20_layout(model, bases: V20Bases, view: bytes) -> dict:
    """The binding of ``newplayer.play`` to ``model``'s discovered V20 build: the tables'
    addresses, and ``init_<variable>`` for each play variable as the init left it in ``view``.
    """
    base = model.init_addr

    def byte(offset: int, voice: int = 0) -> int:
        return view[(base + offset + voice) & 0xFFFF]

    voices = range(3)
    layout = {
        "pitch": bases.pitch,
        "wave_note": bases.wave_note,
        "wave_ctrl": bases.wave_ctrl,
        "filter": bases.filterprog,
        "pulse": bases.pwprog,
        "instruments": bases.instruments,
        "patlo": bases.patternptr_lo,
        "pathi": bases.patternptr_hi,
        "cmdparam": bases.cmdparam,
        "rearm_ad": bases.rearm_ad,
        "rearm_sr": bases.rearm_sr,
        "hardrestart": bases.hardrestart,
    }
    layout.update({f"init_{n}": byte(o) for n, o in SHARED_BYTES.items()})
    layout.update(
        {f"init_{n}": [byte(o, x) for x in voices] for n, o in VOICE_BYTES.items()}
    )
    layout.update(
        {
            f"init_{n}": [byte(lo, x) | byte(hi, x) << 8 for x in voices]
            for n, (lo, hi) in VOICE_WORDS.items()
        }
    )
    return layout


def play_variables(
    bases: V20Bases, base: int, memory: Callable[[int], int]
) -> Iterator[Tuple[str, int]]:
    """``(state name, value)`` of every play variable as the 6502 routine keeps it in the work
    area at ``base`` (``memory`` reads a 6502 run's): what the spec's state holds."""

    def scaled(name: str, value: int) -> int:
        return value >> VARIABLE_SHIFTS.get(name, 0)

    for name, offset in SHARED_BYTES.items():
        if name == "fpos":
            yield "fptr", bases.filterprog + memory(base + offset)
        else:
            yield name, scaled(name, memory(base + offset))
    for voice in range(3):
        for name, offset in VOICE_BYTES.items():
            value = memory(base + offset + voice)
            if name == "pwcur":
                yield f"voice[{voice}].pwrow", bases.pwprog + value
            else:
                yield f"voice[{voice}].{name}", scaled(name, value)
        for name, (lo, hi) in VOICE_WORDS.items():
            if name not in ("start", "rst"):
                word = memory(base + lo + voice) | memory(base + hi + voice) << 8
                yield f"voice[{voice}].{name}", word


def v0x_layout(song: Song, view: bytes) -> dict:
    """The binding of ``v0x.play`` to a V0x ``song``: its tables and gate immediates, and
    ``init_<variable>`` for each play variable as the init left it in ``view``."""
    base = song.load_addr

    def byte(offset: int, voice: int = 0) -> int:
        return view[(base + offset + voice) & 0xFFFF]

    voices = range(3)
    layout = {
        "load": base,
        "subptr_lo": song.subptr_lo,
        "subptr_hi": song.subptr_hi,
        "gate_ctrl": song.gate_ctrl,
        "gateoff_ctrl": song.gateoff_ctrl,
    }
    layout.update({f"init_{n}": byte(o) for n, o in V0X_SHARED.items()})
    layout.update(
        {f"init_{n}": [byte(o, x) for x in voices] for n, o in V0X_BYTES.items()}
    )
    layout.update(
        {
            f"init_{n}": [byte(lo, x) | byte(hi, x) << 8 for x in voices]
            for n, (lo, hi) in V0X_WORDS.items()
        }
    )
    return layout


def v0x_refused(song: Song) -> bool:
    """Whether the spec cannot place ``song``: a voice's SID registers are not ``7 * x`` on."""
    return any(song.image[V0X_VOICE_REGISTERS + v] != 7 * v for v in range(3))


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
    by ``EmuPlayer``.  With ``validate`` each stream the spec opens is parsed
    against the song grammar; :attr:`~pysidtracker.playspec.SpecPlayer.validator` reports the malformed ones.
    """

    MASK_PW_HI = True

    def __init__(self, model, subtune: int = 0, validate: bool = False):
        self.engine, name, bind = _engine(model)
        super().__init__(
            spec(name),
            model.image,
            model.load_addr,
            model.init_addr,
            model.play_addr,
            subtune,
            bind=bind,
            validate=validate,
        )


def _engine(model) -> tuple:
    """``(engine, spec name, bind)`` of a V0x song or a :func:`playable` V20 model, ``bind``
    giving the layout from the memory after the init."""
    if isinstance(model, Song):
        if v0x_refused(model):
            raise SidParseError(
                "JCH NewPlayer V0x with voice registers off 7 * voice has no spec; "
                "make_player plays it on EmuPlayer"
            )
        return "v0x", "v0x", lambda view, entry: v0x_layout(model, view)
    if isinstance(model, NewPlayerModel):
        bases = playable(model)
        if bases is None:
            raise SidParseError(
                f"JCH NewPlayer {model.version} has no spec; "
                "make_player plays it on EmuPlayer"
            )
        return "v20", "newplayer", lambda view, entry: v20_layout(model, bases, view)
    raise SidParseError(f"cannot play a {type(model).__name__}")


def spec_binding(model) -> SpecBinding:
    """The :class:`~pysidtracker.formats.SpecBinding` of ``model`` at its own subtune:
    the packaged ``v0x.play`` or ``newplayer.play`` and its layout, as
    :class:`JchPlayer` plays it."""
    _, name, bind = _engine(model)
    return SpecBinding(spec(name), bind, model.image, subtune=model.subtune)


def make_player(model, subtune: Optional[int] = None) -> MemPlayer:
    """The byte-exact player of ``model``'s ``subtune`` (default: its own).

    A :class:`JchPlayer` for a V0x :class:`~pyjch.model.Song` or a
    :func:`playable` V20 model; :class:`~pysidtracker.EmuPlayer` running the
    tune's own driver for any other recovered NewPlayer family build.
    """
    if subtune is None:
        subtune = model.subtune
    if (isinstance(model, NewPlayerModel) and playable(model) is None) or (
        isinstance(model, Song) and v0x_refused(model)
    ):
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
