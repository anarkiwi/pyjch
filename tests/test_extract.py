"""Offline extraction tests against the synthetic V20 image.

Every expected value here is derived from the known bytes hand-authored in
:mod:`tests._synth_v20`; the extractor decodes them with the same semantics the
V20 player applies at runtime.
"""

from pyjch import newplayer, reader
from pyjch.extract import extract
from pyjch.songmodel import CommandKind, NoteKind, Tune

from tests import _synth_v20 as synth
from tests.test_newplayer import INIT, LOAD, PLAY, _base_image


def _tune() -> Tune:
    return extract(reader.parse(synth.build_psid()))


def _family_tune() -> Tune:
    model = newplayer.recover(
        LOAD, INIT, PLAY, "n", "a", "r", _base_image(), b"", "V14", 1
    )
    return extract(model)


def test_family_tier_decodes_subset_and_notes_absences():
    tune = _family_tune()
    assert tune.provenance.tier == "family"
    assert tune.hard_restart is None
    # subtunes / order lists / patterns / instruments still recovered
    assert tune.subtunes and tune.instruments
    assert [e.pattern for e in tune.subtunes[0].order_lists[0].entries] == [0, 1]
    # absent tables are honestly reported, none fabricated
    assert not tune.pw_program and not tune.filter_program and not tune.commands
    assert tune.wavetable is None
    assert any("not recovered" in note for note in tune.provenance.notes)


def test_provenance_is_v20():
    tune = _tune()
    assert tune.provenance.tier == "v20"
    assert tune.provenance.bases["cmdparam"] == synth.CMDPARAM
    assert tune.hard_restart == 0x09
    assert tune.load_addr == synth.LOAD


def test_subtunes_and_order_lists():
    tune = _tune()
    assert len(tune.subtunes) == 1
    sub = tune.subtunes[0]
    assert sub.tempo == 0x03
    assert [e.pattern for e in sub.order_lists[0].entries] == [0, 1, 4]
    assert all(e.transpose == 0 for e in sub.order_lists[0].entries)
    assert sub.order_lists[0].loops
    # voice 1 carries the $85 transpose prefix on its single entry.
    entry = sub.order_lists[1].entries[0]
    assert entry.pattern == 2 and entry.transpose == 0x85
    assert [e.pattern for e in sub.order_lists[2].entries] == [3]


def test_patterns_decode_every_event_kind():
    patterns = _tune().patterns
    assert len(patterns) == 5

    def kinds(events):
        out = []
        for event in events:
            if event.note is not None:
                out.append(event.note.kind)
            else:
                out.append(event.command.kind)
        return out

    assert kinds(patterns[0]) == [CommandKind.PORTAMENTO, NoteKind.NOTE]
    assert kinds(patterns[1]) == [CommandKind.VIBRATO, NoteKind.NOTE]
    assert kinds(patterns[2]) == [CommandKind.INSTRUMENT, NoteKind.NOTE, NoteKind.REST]
    assert kinds(patterns[3]) == [NoteKind.HOLD]
    assert kinds(patterns[4]) == [
        CommandKind.DURATION,
        CommandKind.TEMPO,
        CommandKind.VOLUME,
        CommandKind.AD_OVERRIDE,
        CommandKind.WAVE_PATCH,
        NoteKind.NOTE,
    ]
    # decoded operands
    dur = patterns[4][0].command
    assert dur.arg == 0x03 and dur.delay is False
    inst = patterns[2][0].command
    assert inst.arg == 1
    assert patterns[0][1].note.value == 0x0C


def test_instruments_decode_fields():
    insts = _tune().instruments
    assert len(insts) == 4
    i0 = insts[0]
    assert (i0.ad, i0.sr, i0.wave_speed, i0.abs_freq, i0.flag7) == (
        0x22,
        0x33,
        1,
        False,
        False,
    )
    assert (i0.filter_mode, i0.filter_route, i0.filter_prog) == (0x10, 1, 0)
    i1 = insts[1]
    assert i1.abs_freq is True and i1.filter_route == 8 and i1.pw_prog == 2
    i2 = insts[2]
    assert (i2.wave_speed, i2.pw_prog, i2.wave_start, i2.wave_start2) == (2, 4, 2, 3)
    assert insts[3].flag7 is True


def test_wavetable_columns():
    wave = _tune().wavetable
    assert wave.note_col[:10] == [0x0C, 0x10, 0x7E, 0x14, 0x7F, 0, 0x08, 0x90, 0, 0]
    assert wave.ctrl_col[:10] == [0x41, 0x21, 0x11, 0x81, 0x02, 0x40, 0x40, 0x09, 0, 0]
    assert len(wave.note_col) == len(wave.ctrl_col) == 0x40


def test_pw_filter_programs_and_groove():
    tune = _tune()
    assert [(s.reset, s.step, s.dir_rate, s.next_off) for s in tune.pw_program] == [
        (0x08, 0x08, 0x03, 0x04),
        (0x02, 0x30, 0x10, 0x00),
        (0xFF, 0x10, 0x84, 0x00),
    ]
    assert [(s.value, s.step, s.dwell, s.next_off) for s in tune.filter_program] == [
        (0x00, 0x02, 0x01, 0x04),
        (0xA0, 0xFF, 0x01, 0x08),
        (0x30, 0x02, 0x01, 0x00),
    ]
    assert tune.groove == [0x00, 0x02]


def test_commands_and_pitch():
    tune = _tune()
    assert [c.kind for c in tune.commands] == [
        CommandKind.PORTAMENTO,
        CommandKind.PORTAMENTO,
        CommandKind.VIBRATO,
        CommandKind.TEMPO,
        CommandKind.VOLUME,
        CommandKind.AD_OVERRIDE,
        CommandKind.WAVE_PATCH,
    ]
    assert tune.commands[3].lo == 0xE0 and tune.commands[3].hi == 0x04
    assert tune.pitch_table == list(range(0x40))


def test_v0x_song_out_of_scope():
    import pytest

    from pyjch.errors import SidParseError
    from pyjch.model import Song

    with pytest.raises(SidParseError):
        extract(Song())
