"""Read a JCH NewPlayer tune (PSID/RSID/.sid or .prg image) into a Song.

A PSID/RSID container wraps the raw C64 image (player code + song data)
with a header giving the load/init/play addresses.  The JCH NewPlayer
binary is identical across tunes of the *same player version*; only its
DATA differs, and each per-tune immediate / pointer-table base lives in
the player code as an instruction operand.  The reader locates those
operands by their surrounding instruction bytes -- an idiom search rather
than a fixed offset -- so it is robust both to relocation *and* to the
small per-tune code shifts that a fixed offset cannot survive.

Supported version.  HVSC labels (via ``sidid``) at least twenty distinct
JCH ``NewPlayer`` binaries (V1..V21, plus ``Glover_NewPlayer_V21`` and
``JCH_DigiPlayer``); they are genuinely different players, not just
relocations.  This reader/player pair models the canonical
``JCH_NewPlayer_V0x`` layout: attack/decay and sustain/release seeded by
immediates (``LDA #imm ; STA $D405/$D406``), gate-on and gate-off CTRL
seeded by immediates (``LDA #imm ; STA $D404,Y``), and split subpattern
pointer tables loaded via ``LDA abs,Y ; STA $fb/$fc``.  :func:`parse`
discovers those idioms; when they are absent it raises -- it does not
silently return garbage from a foreign player.  See ``docs/versions.md``.
"""

from typing import Any, Optional, Tuple

from pysidtracker import BaseSidParser, CodePattern, SidImage, find_code_all
from pysidtracker import SidParseError as _BaseSidParseError
from pysidtracker import find_code_first, read_bytes, resolve_entry_points

from pyjch import constants, newplayer
from pyjch.errors import SidParseError
from pyjch.model import Song


def _parse_container(
    data: bytes,
) -> Tuple[int, int, int, str, str, str, bytes, bytes, SidImage]:
    """Return (load, init, play, name, author, released, image, header, img)."""
    try:
        img = SidImage.from_bytes(data)
    except _BaseSidParseError as exc:
        raise SidParseError(str(exc)) from exc
    header = img.header
    load = img.load
    # Header init/play of 0 mean "same as load"; a bare .prg has no header, so
    # the JCH NewPlayer defaults apply.
    init, play = resolve_entry_points(
        header, load, constants.DEFAULT_INIT, constants.DEFAULT_PLAY
    )
    if header is None:
        return (load, init, play, "", "", "", img.image, img.container, img)
    return (
        load,
        init,
        play,
        header.name,
        header.author,
        header.released,
        img.image,
        img.container,
        img,
    )


def _byte(image: bytes, off: int) -> int:
    if 0 <= off < len(image):
        return image[off]
    raise SidParseError(f"operand offset {off:#x} past end of image")


# Player-code idioms carrying the per-tune values as captured operands (0.2.0
# ``codescan`` masked patterns; ``{op}`` = immediate byte, ``{op:w}`` = 16-bit LE
# operand).  The SID store targets are ``$D405``/``$D406`` (AD/SR), ``$D404,Y``
# (CTRL gate-off then gate-on) and the ``$FB``/``$FC`` zero-page pointer.
_AD_PAT = CodePattern("A9 {op} 8D 05 D4")  # LDA #imm ; STA $D405 (attack/decay)
_SR_PAT = CodePattern("A9 {op} 8D 06 D4")  # LDA #imm ; STA $D406 (sustain/release)
_GATE_PAT = CodePattern("A9 {op} 99 04 D4")  # LDA #imm ; STA $D404,Y (CTRL)
_SUBLO_PAT = CodePattern("B9 {op:w} 85 FB")  # LDA abs,Y ; STA $FB (ptr-lo base)
_SUBHI_PAT = CodePattern("B9 {op:w} 85 FC")  # LDA abs,Y ; STA $FC (ptr-hi base)


def _one_operand(img: SidImage, pattern: CodePattern, what: str) -> int:
    """The first ``pattern`` operand capture, or raise if the idiom is absent."""
    match = find_code_first(img, pattern)
    if match is None:
        raise SidParseError(
            f"not a canonical JCH NewPlayer (V0x): {what} idiom not found"
        )
    return match.captures["op"]


def _parse_v0x(  # pylint: disable=too-many-arguments
    load: int,
    init: int,
    play: int,
    name: str,
    author: str,
    released: str,
    image: bytes,
    header: bytes,
    img: SidImage,
) -> Song:
    """Parse the canonical ``JCH_NewPlayer_V0x`` layout into a byte-exact Song.

    Raises :class:`~pyjch.errors.SidParseError` when the image is not the V0x
    layout this player models (the discovery idioms are absent) -- rather than
    returning meaningless values read out of a different player's code.
    """
    # Per-tune DISCOVERY via instruction idioms (relocation- and shift-safe).
    init_ad = _one_operand(img, _AD_PAT, "attack/decay init")
    init_sr = _one_operand(img, _SR_PAT, "sustain/release init")
    gates = [m.captures["op"] for m in find_code_all(img, _GATE_PAT)]
    if len(gates) < 2:
        raise SidParseError(
            "not a canonical JCH NewPlayer (V0x): gate-on/off CTRL immediates "
            f"not found (need 2, found {len(gates)})"
        )
    gateoff_ctrl, gate_ctrl = gates[0], gates[1]
    subptr_lo = _one_operand(img, _SUBLO_PAT, "subpattern ptr-lo base")
    subptr_hi = _one_operand(img, _SUBHI_PAT, "subpattern ptr-hi base")

    freq_lo = [
        _byte(image, constants.FREQ_LO + n) for n in range(constants.FREQ_TABLE_LEN)
    ]
    freq_hi = [
        _byte(image, constants.FREQ_HI + n) for n in range(constants.FREQ_TABLE_LEN)
    ]

    return Song(
        load_addr=load,
        init_addr=init,
        play_addr=play,
        name=name,
        author=author,
        released=released,
        init_ad=init_ad,
        init_sr=init_sr,
        gate_ctrl=gate_ctrl,
        gateoff_ctrl=gateoff_ctrl,
        subptr_lo=subptr_lo,
        subptr_hi=subptr_hi,
        orderlist_ptr_table=load + constants.ORDERLIST_PTR_TABLE,
        freq_lo=freq_lo,
        freq_hi=freq_hi,
        image=image,
        container_header=header,
    )


def parse(data: bytes):
    """Parse a JCH NewPlayer tune (PSID/RSID/.sid or .prg) into its model.

    Returns a :class:`~pyjch.model.Song` for the canonical, byte-exact-replay
    ``JCH_NewPlayer_V0x`` layout, or a :class:`~pyjch.newplayer.NewPlayerModel`
    for the later wavetable-family versions (V1/V2/V3/V6/V8/V9/V10/V11/V13/
    V14/V15/V17/V18/V20) whose song DATA this reader recovers (playback not
    byte-exact-verified; see :mod:`pyjch.newplayer`).

    Raises :class:`~pyjch.errors.SidParseError` when the image is neither --
    rather than returning meaningless values out of a foreign player's code.
    """
    load, init, play, name, author, released, image, header, img = _parse_container(
        data
    )
    try:
        return _parse_v0x(load, init, play, name, author, released, image, header, img)
    except SidParseError as v0x_exc:
        num_subtunes = _subtune_count(data)
        try:
            return newplayer.recover(
                load,
                init,
                play,
                name,
                author,
                released,
                image,
                header,
                version=newplayer.classify_version(load, image, play),
                num_subtunes=num_subtunes,
            )
        except SidParseError as family_exc:
            raise SidParseError(
                f"not a supported JCH NewPlayer tune: {v0x_exc}; {family_exc}"
            ) from family_exc


def _subtune_count(data: bytes) -> int:
    """Number of subtunes from the PSID/RSID header (1 for a bare .prg)."""
    try:
        img = SidImage.from_bytes(data)
    except _BaseSidParseError:
        return 1
    header = img.header
    return getattr(header, "songs", 1) or 1 if header is not None else 1


def read(src):
    """Read a JCH NewPlayer tune from a path, bytes, or file-like object."""
    return parse(read_bytes(src))


# Canonical JCH_NewPlayer_V0x init signature: STA $D405 ; STA $D40C (seed the
# attack/decay default into voices 0 and 1).  The SID register addresses are
# load-independent, so this anchor survives relocation; empirically it is
# present in every V0x tune and absent from the other NewPlayer versions.
_RECOGNIZE_PAT = CodePattern("8D 05 D4 8D 0C D4")


class JchSidParser(BaseSidParser):
    """:class:`~pysidtracker.BaseSidParser` adapter for the JCH NewPlayer.

    Gives the JCH reader the shared ``read``/``parse``/``detect`` surface.
    :meth:`recognize` anchors on either the canonical ``JCH_NewPlayer_V0x``
    init signature (a load-independent SID-register store idiom) or a coherent
    NewPlayer wavetable-family layout, so :meth:`detect` classifies a tune whose
    song model this reader recovers statically as ``DIRECT`` and reports
    ``UNKNOWN`` for foreign players / unrecoverable versions.  ``DIRECT`` here
    means the song model is recovered directly from the loaded image; it does
    not assert byte-exact replay of the family versions (only V0x is that).
    """

    error_class: type = SidParseError

    def parse(self, data: bytes, **kwargs: Any):
        """Decode raw ``.sid``/``.prg`` ``data`` into its recovered song model."""
        return parse(data, **kwargs)

    def recognize(self, image: SidImage) -> Optional[object]:
        """Return a V0x or family anchor address in ``image``, else ``None``."""
        match = find_code_first(image, _RECOGNIZE_PAT)
        if match is not None:
            return match.addr
        header = image.header
        init = (header.init_address or image.load) if header else image.load
        play = (header.play_address or image.load) if header else image.load
        return newplayer.recognize(image.load, image.image, init, play)
