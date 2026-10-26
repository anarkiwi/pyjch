"""The JCH NewPlayer build as a :class:`~pysidtracker.Variant` of detected facets.

A parsed model (a V0x :class:`~pyjch.model.Song` or a family
:class:`~pyjch.newplayer.NewPlayerModel`) is one *build* of the player.  Each axis
the builds differ on is a :class:`~pysidtracker.Facet`: its value and where it was
read (``detected@$addr``), defaulted, or inferred.  :func:`detect_variant` is the
``variant`` hook of the registered :class:`~pysidtracker.SidFormat`, so the
survey ``info`` and the whydiff / pathdiff site labels come from it, and
:class:`~pyjch.songmodel.Provenance` records the same facets.

Facets:

* ``model`` -- ``v0x`` (canonical layout) or ``family`` (wavetable family);
* ``version`` -- ``V0x``, or the play-prologue class tag of a family build;
* ``engine`` -- the byte-exact player: native ``v0x`` / ``v20``, else ``emu``;
* ``v20_gate`` -- ``reference`` when the V20 native engine accepts the build,
  else the sub-build gate that rejected it;
* ``hard_restart`` -- the hard-restart CTRL immediate (``$09`` when absent);
* ``wave_layout`` / ``pitch_form`` / ``ctrl_shadow_form`` -- how the wave table,
  pitch table and CTRL-shadow blit are laid out;
* ``entry_points`` -- ``jmp-vectors`` (a ``JMP init ; JMP play`` pair) or
  ``direct``; the source is ``default`` for a bare ``.prg``;
* ``v0x_layout`` -- ``fixed`` for the V0x layout (its work cells and tables sit
  at the fixed offsets of :mod:`pyjch.constants`), else ``none``.
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional

from pysidtracker import Facet, FacetRegistry, SidImage, Variant, find_code_first
from pysidtracker import is_jmp_vector
from pysidtracker.facets import DEFAULT_SOURCE, Finding, detected, inferred

from pyjch import newplayer
from pyjch.model import Song
from pyjch.newplayer import NewPlayerModel
from pyjch.player import V20_GATES, V20_REFERENCE, v20_bases
from pyjch.reader import V0X_SIGNATURE

MODELS = ("v0x", "family")
ENGINES = ("v0x", "v20", "emu")
ENTRY_JMP = "jmp-vectors"
ENTRY_DIRECT = "direct"
NOT_APPLICABLE = "n/a"
V0X_FIXED = "fixed"
V0X_NONE = "none"
DEFAULT_HARD_RESTART = 0x09  # the V20 hard-restart CTRL when no immediate is found
_PRG_CONTAINER = 2  # a bare .prg's container is its two-byte load address


@dataclass(frozen=True)
class _Context:
    """What the detectors look at: the model, its image and its V20 gate."""

    model: Any
    image: SidImage
    gate: str
    engine: str

    @property
    def is_family(self) -> bool:
        """True for a wavetable-family model (not the V0x ``Song``)."""
        return isinstance(self.model, NewPlayerModel)


def _context(model) -> _Context:
    if isinstance(model, Song):
        image = newplayer.image_of(model.load_addr, model.image)
        return _Context(model, image, NOT_APPLICABLE, "v0x")
    if not isinstance(model, NewPlayerModel):
        raise TypeError(f"cannot detect a variant of {type(model).__name__}")
    bases, gate = v20_bases(model)
    return _Context(model, model.sid, gate, "emu" if bases is None else "v20")


def _model(ctx: _Context) -> Finding:
    if ctx.is_family:
        return Finding("family", inferred("wavetable-family layout"))
    sig = find_code_first(ctx.image, V0X_SIGNATURE)
    return Finding(
        "v0x",
        inferred("parsed layout") if sig is None else detected(sig.addr),
    )


def _version(ctx: _Context) -> Optional[Finding]:
    if not ctx.is_family:
        return Finding("V0x", inferred("v0x layout"))
    model = ctx.model
    found = newplayer.prologue_class(ctx.image, model.init_addr, model.play_addr)
    return None if found is None else Finding(found[0], detected(found[1]))


def _engine(ctx: _Context) -> Finding:
    return Finding(ctx.engine, inferred(f"v20 gate {ctx.gate}"))


def _v20_gate(ctx: _Context) -> Optional[Finding]:
    if not ctx.is_family:
        return None
    if ctx.gate == V20_REFERENCE:
        return Finding(ctx.gate, detected(ctx.model.init_addr))
    return Finding(ctx.gate, inferred("v20 idioms"))


def _hard_restart(ctx: _Context) -> Optional[Finding]:
    if not ctx.is_family:
        return Finding(None, inferred("v0x has no hard restart"))
    site = newplayer.hard_restart_site(ctx.image)
    return None if site is None else Finding(site.captures["op"], detected(site.addr))


def _form(name: str):
    def detect(ctx: _Context) -> Optional[Finding]:
        if not ctx.is_family:
            return None
        form = ctx.model.forms.get(name)
        if form is None:
            return None
        value, addr = form
        return Finding(value, detected(addr))

    return detect


def _entry(ctx: _Context) -> Finding:
    model = ctx.model
    jump = is_jmp_vector(ctx.image, model.init_addr) and is_jmp_vector(
        ctx.image, model.play_addr
    )
    value = ENTRY_JMP if jump else ENTRY_DIRECT
    if len(model.container_header) <= _PRG_CONTAINER:  # no header: defaults apply
        return Finding(value, DEFAULT_SOURCE)
    return Finding(value, detected(model.init_addr))


def _v0x_layout(ctx: _Context) -> Optional[Finding]:
    if ctx.is_family:
        return None
    sig = find_code_first(ctx.image, V0X_SIGNATURE)
    return Finding(
        V0X_FIXED, inferred("fixed offsets") if sig is None else detected(sig.addr)
    )


FACETS = FacetRegistry(
    [
        Facet("model", "build", MODELS, detector=_model),
        Facet(
            "version", "release", default=newplayer.DEFAULT_VERSION, detector=_version
        ),
        Facet("engine", "build", ENGINES, detector=_engine),
        Facet(
            "v20_gate", "build", (*V20_GATES, NOT_APPLICABLE), NOT_APPLICABLE, _v20_gate
        ),
        Facet("hard_restart", "build", None, DEFAULT_HARD_RESTART, _hard_restart),
        Facet(
            "wave_layout",
            "layout",
            (
                newplayer.WAVE_COLUMNS,
                newplayer.WAVE_NOTE_ONLY,
                newplayer.WAVE_STREAM,
                newplayer.WAVE_NONE,
            ),
            newplayer.WAVE_NONE,
            _form("wave"),
        ),
        Facet(
            "pitch_form",
            "layout",
            (
                newplayer.PITCH_ADC_CARRY,
                newplayer.PITCH_INTERLEAVED,
                newplayer.PITCH_NONE,
            ),
            newplayer.PITCH_NONE,
            _form("pitch"),
        ),
        Facet(
            "ctrl_shadow_form",
            "layout",
            (newplayer.SHADOW_V13, newplayer.SHADOW_V9, newplayer.SHADOW_NONE),
            newplayer.SHADOW_NONE,
            _form("ctrl_shadow"),
        ),
        Facet("entry_points", "init", (ENTRY_JMP, ENTRY_DIRECT), ENTRY_DIRECT, _entry),
        Facet("v0x_layout", "layout", (V0X_FIXED, V0X_NONE), V0X_NONE, _v0x_layout),
    ]
)


def detect_variant(model) -> Variant:
    """The :class:`~pysidtracker.Variant` of a parsed ``model``."""
    ctx = _context(model)
    return FACETS.detect(ctx, family="family" if ctx.is_family else "v0x")


def variant_info(model) -> Dict[str, Any]:
    """``detect_variant(model).as_info()``: facet values plus ``<facet>.src``."""
    return detect_variant(model).as_info()


def facet_sites(model) -> Dict[int, str]:
    """``{addr: facet}``: the code address each facet was detected at."""
    variant = detect_variant(model)
    return {
        variant.source(name).addr: name
        for name in variant
        if variant.source(name).addr is not None
    }
