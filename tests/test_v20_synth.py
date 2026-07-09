"""Offline unit tests for the V20 player + NewPlayer reader.

These use a hand-authored synthetic V20 image (:mod:`tests._synth_v20`) and need
no ``$HVSC`` tree, no real tune and no pre-generated oracle -- they drive the
reader/player through their code paths and assert structural results, so they
exercise ``v20player`` / ``newplayer`` in a bare CI environment.  The byte-exact
guarantee is asserted separately by the HVSC/py65 oracle tests.
"""

import pytest

from pyjch import reader, reglog, v20player
from pyjch.errors import SidParseError
from pyjch.newplayer import NewPlayerModel

from tests import _synth_v20 as synth


def _model():
    return reader.parse(synth.build_psid())


def test_synth_parses_to_coherent_model():
    model = _model()
    assert isinstance(model, NewPlayerModel)
    assert model.load_addr == synth.LOAD
    assert model.subtune_table == synth.SUBTUNE
    assert model.wave_note_col == synth.WAVE_NOTE
    assert model.instruments == synth.INSTRUMENTS
    assert model.pitch_table == synth.PITCH
    # order lists walk to a terminator through in-range pattern pointers
    for voice in range(3):
        idxs = model.iter_orderlist(0, voice)
        assert idxs
        for idx in idxs:
            assert synth.LOAD <= model.pattern_ptr(idx) < synth.LOAD + synth.IMAGE_LEN
    assert model.instrument(0) is not None and len(model.instrument(0)) == 8
    assert model.subtune_tempo(0) == 0x03


def test_synth_playable_discovers_all_bases():
    bases = v20player.playable(_model())
    assert bases is not None
    assert bases.wave_ctrl == synth.WAVE_CTRL
    assert bases.filterprog == synth.FILTERPROG
    assert bases.pwprog == synth.PWPROG
    assert bases.cmdparam == synth.CMDPARAM
    assert bases.instruments == synth.INSTRUMENTS
    assert bases.rearm_ad == synth.CMDPARAM
    assert bases.hardrestart == 0x09


def test_synth_player_runs_and_writes():
    model = _model()
    player = v20player.V20Player(model)
    changed = 0
    for frame in range(400):
        writes = player.play_frame()
        row = player.grid_row()
        assert len(row) == 25
        if frame == 0:
            assert len(writes) == 25  # first frame emits the whole register file
        changed += len(writes)
    # a running tune keeps changing registers frame to frame
    assert changed > 400


def test_synth_render_grid():
    grid = v20player.render_grid(_model(), 128)
    assert len(grid) == 128
    assert all(len(row) == 25 for row in grid)
    # the tune is not silent: some register varies across the render
    assert any(grid[i] != grid[0] for i in range(1, 128))


def test_synth_hard_restart_immediate_is_discovered():
    model = reader.parse(synth.build_psid(hard_restart=0x11))
    bases = v20player.playable(model)
    assert bases is not None and bases.hardrestart == 0x11


def test_synth_reglog_and_make_player():
    model = _model()
    assert isinstance(reglog.make_player(model), v20player.V20Player)
    writes = list(reglog.iter_register_writes(model, max_frames=16))
    assert writes[0].clock == 0
    baseline = {
        w.reg for w in writes if w.clock < reglog.constants.PAL_CYCLES_PER_FRAME
    }
    assert baseline == set(range(25))


def _mutated_model(mutate):
    """Parse a synthetic image after ``mutate(bytearray_image)`` edits it."""
    image, _load = synth.build_image()
    buf = bytearray(image)
    mutate(buf)
    header = bytearray(synth.build_psid()[:0x7C])
    return reader.parse(bytes(header) + bytes(buf))


@pytest.mark.parametrize(
    "idiom",
    [
        b"\x9d\x5d\x17",  # wave-ctrl store
        b"\x8d\x46\x17",  # filter-program store
        b"\x9d\x81\x17",  # PW-program store
    ],
)
def test_missing_v20_idiom_rejects(idiom):
    """Removing a required V20 idiom drops the tune to model-recovered only."""

    def mutate(buf):
        pos = buf.index(idiom)
        buf[pos : pos + len(idiom)] = b"\xea" * len(idiom)

    model = _mutated_model(mutate)
    assert isinstance(model, NewPlayerModel)
    assert v20player.playable(model) is None
    with pytest.raises(SidParseError):
        reglog.make_player(model)


def test_wrong_instrument_field_layout_rejects():
    """A sub-build whose wave-flags field is at +3 (not +2) is gated out."""

    def mutate(buf):
        pos = buf.index(b"\xb9" + synth._w(synth.INSTRUMENTS + 2) + b"\x48\x29\x80")
        buf[pos + 1 : pos + 3] = synth._w(synth.INSTRUMENTS + 3)

    model = _mutated_model(mutate)
    assert v20player.playable(model) is None


def test_split_wave_note_column_rejects():
    """A note-path wave-note column that differs from the abs-path one is gated."""

    def mutate(buf):
        pos = buf.index(b"\xbc\x95\x17\xb9" + synth._w(synth.WAVE_NOTE) + b"\x30")
        buf[pos + 4 : pos + 6] = synth._w(synth.WAVE_NOTE + 0x40)

    model = _mutated_model(mutate)
    assert v20player.playable(model) is None


def test_bad_init_vector_rejects():
    """An init entry that is not a JMP is not a playable V20 build."""
    image, load = synth.build_image()
    buf = bytearray(image)
    buf[0] = 0x00  # init no longer a JMP opcode
    model = NewPlayerModel(
        load_addr=load,
        init_addr=0x1000,
        play_addr=0x1003,
        image=bytes(buf),
        subtune_table=synth.SUBTUNE,
        wave_note_col=synth.WAVE_NOTE,
        instruments=synth.INSTRUMENTS,
        patternptr_lo=synth.PATLO,
        patternptr_hi=synth.PATHI,
        pitch_table=synth.PITCH,
    )
    assert v20player.playable(model) is None
    with pytest.raises(ValueError):
        v20player.V20Player(model)
