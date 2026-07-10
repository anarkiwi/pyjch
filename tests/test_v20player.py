"""Byte-exact validation of the JCH NewPlayer V20 player.

The recovered :class:`~pyjch.newplayer.NewPlayerModel` of a V20 build is driven
by :class:`~pyjch.v20player.V20Player` and checked **frame-exact** against a
clean-room py65 register oracle (:mod:`tests._v20oracle`) -- the same
grid-comparison contract the V0x player uses against ``preframr-sidtrace``.

House rules: tunes are HVSC copyright works, never committed (resolved from a
local ``$HVSC`` tree or the gitignored fetch cache, else the test skips); the
ground-truth grid is this repo's own py65 output, generated in-process.
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import fetch_tunes  # noqa: E402  (after sys.path tweak)

from pyjch import reader, reglog, v20player  # noqa: E402
from pyjch.errors import SidParseError  # noqa: E402
from pyjch.newplayer import NewPlayerModel  # noqa: E402

from tests._v20oracle import oracle_grid  # noqa: E402

# Deterministic V20 representatives across builds/authors: the RE reference
# (contiguous tables), a relocated-table demo, and other-author tunes.
V20_TUNES = [
    "MUSICIANS/D/DRAX/Worktunes/24th_Amaranth_Grand_Prix_3.sid",
    "DEMOS/0-9/7D_Funkt.sid",
    "MUSICIANS/D/DRAX/Stories.sid",
    "MUSICIANS/G/G-Fellow/Draxme.sid",
]

# NewPlayer family tunes that are NOT the V20 build (different player) -- the
# gate must keep them at the honest "model recovered" tier.
NON_V20_FAMILY = [
    "DEMOS/A-F/Fjellaporna.sid",  # V14
    "MUSICIANS/A/Abaddon/Apina.sid",  # V13
]

FRAMES = 150


def _resolve(relpath):
    for env in ("HVSC", "JCH_LOCAL_HVSC"):
        base = os.environ.get(env)
        if base and (Path(base) / relpath).exists():
            return Path(base) / relpath
    dest = fetch_tunes.CACHE / relpath
    if dest.exists():
        return dest
    if os.environ.get("PYJCH_FETCH_CORPUS"):
        try:
            return fetch_tunes.fetch(relpath)
        except Exception:  # pylint: disable=broad-except
            return None
    return None


def _load(relpath):
    path = _resolve(relpath)
    if path is None:
        pytest.skip(f"{relpath} unavailable (no local HVSC, not cached)")
    return Path(path).read_bytes()


@pytest.mark.parametrize("relpath", V20_TUNES)
def test_v20_frame_exact(relpath):
    """A V20 tune parses to a playable model and replays frame-exact vs py65."""
    data = _load(relpath)
    model = reader.parse(data)
    assert isinstance(model, NewPlayerModel)
    assert v20player.playable(model) is not None
    oracle = oracle_grid(data, FRAMES)
    rendered = v20player.render_grid(model, FRAMES)
    assert len(rendered) == len(oracle) == FRAMES
    for frame, (want, got) in enumerate(zip(oracle, rendered)):
        assert got == want, f"frame {frame}: oracle={want} player={got}"


@pytest.mark.parametrize("relpath", NON_V20_FAMILY)
def test_non_v20_family_not_playable(relpath):
    """A non-V20 family model is recovered but gated out of byte-exact playback."""
    data = _load(relpath)
    model = reader.parse(data)
    assert isinstance(model, NewPlayerModel)
    assert v20player.playable(model) is None
    with pytest.raises(SidParseError):
        reglog.make_player(model)


def test_v20_reglog_matches_render():
    """The V20 register-log baseline + first play frame agree with the grid."""
    data = _load(V20_TUNES[0])
    model = reader.parse(data)
    if v20player.playable(model) is None:  # pragma: no cover - resolved above
        pytest.skip("model not V20 in this tree")
    writes = list(reglog.iter_register_writes(model, max_frames=8))
    # init baseline burst (clocks below one frame) covers all 25 registers;
    # play frames follow one cycle-gap later.
    assert writes[0].clock == 0
    cpf = reglog.constants.PAL_CYCLES_PER_FRAME
    baseline = {w.reg for w in writes if w.clock < cpf}
    assert baseline == set(range(25))
    assert any(w.clock >= cpf for w in writes)
