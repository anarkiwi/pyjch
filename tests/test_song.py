"""The typed song: every variant from its bytes, and real tunes round-tripped."""

import dataclasses

import pytest

from pyjch import decompile_sid
from pyjch import song as S

V20, V0X = S.Dialect.NEWPLAYER, S.Dialect.V0X


@pytest.mark.parametrize(
    "dialect, data, track",
    [
        (
            V20,
            [0x83, 0x05, 0x02, 0xFF],
            ((S.Transpose(3), S.PlaySequence(5), S.PlaySequence(2)), S.Loop()),
        ),
        (V20, [0x00, 0xFE], ((S.PlaySequence(0),), S.Stop())),
        (
            V0X,
            [0x8E, 0x01, 0x80, 0xFF],
            ((S.Transpose(2), S.PlaySequence(1), S.Transpose(-12)), S.Loop()),
        ),
    ],
)
def test_track_ops(dialect, data, track):
    got = S._track(dialect, data)  # pylint: disable=protected-access
    assert (got.ops, got.end) == track
    assert S.track_bytes(dialect, got) == data


ROW = S.Row


@pytest.mark.parametrize(
    "dialect, data, rows",
    [
        (
            V20,
            [0x93, 0xA4, 0xC2, 0x30, 0x00, 0x7E, 0x82, 0x7F],
            (
                ROW(
                    (S.Duration(3, True), S.SelectInstrument(4), S.RunCommand(2)),
                    S.Note(0x30),
                ),
                ROW((), S.GateOff()),
                ROW((), S.Hold()),
                ROW((S.Duration(2),), None),
            ),
        ),
        (
            V0X,
            [0x84, 0x1A, 0x00, 0x7F, 0xFF],
            (
                ROW((S.Duration(4),), S.Note(0x1A)),
                ROW((), S.GateOff()),
                ROW((), S.Hold()),
            ),
        ),
    ],
)
def test_sequence_rows(dialect, data, rows):
    seq = S._sequence(dialect, data)  # pylint: disable=protected-access
    assert seq.rows == rows
    assert S.sequence_bytes(dialect, seq) == data


@pytest.mark.parametrize(
    "data, command",
    [
        ((0x05, 0x10), S.Slide(False, 0x510)),
        ((0x22, 0x40), S.Slide(True, 0x240)),
        ((0x11, 0x00), S.Slide(False, 0x100, 0x10)),
        ((0x63, 0x24), S.Vibrato(3, 2, 4)),
        ((0x91, 0xF8), S.SetSustainRelease(0xF8, 1)),
        ((0xE0, 0x06), S.SetTempo(6)),
        ((0xF2, 0x0F), S.SetVolume(0x0F, 2)),
        ((0x45, 0x30), S.SetInstrumentWave(5, 0x30, 0x40)),
        ((0x3F, 0x01), S.SetInstrumentWave(0x1F, 0x01, 0x20)),
    ],
)
def test_commands(data, command):
    assert S._command(*data) == command  # pylint: disable=protected-access
    assert S.command_bytes(command) == data


def test_instrument_fields():
    rec = [0x09, 0xA8, 0xF3, 0x5A, 0x08, 0x04, 0x10, 0x12]
    ins = S._instrument(rec)  # pylint: disable=protected-access
    assert ins == S.Instrument(
        attack_decay=0x09,
        sustain_release=0xA8,
        hard_restart=True,
        absolute=True,
        wave_speed=3,
        filter_resonance=5,
        filter_mode=0x0A,
        filter_program=8,
        pulse_program=4,
        wave=0x10,
        wave_release=0x12,
        reserved=0x30,
    )
    assert S.instrument_bytes(ins) == rec


@pytest.mark.parametrize(
    "data, row",
    [
        ((0x0C, 0x41), S.WaveStep(0x41, 0x0C)),
        ((0x90, 0x81), S.WaveStep(0x81, 0x10, True)),
        ((0x7E, 0x00), S.WaveHold(0x00)),
        ((0x7F, 0x03), S.WaveJump(3)),
    ],
)
def test_wave_rows(data, row):
    assert S._wave_row(*data) == row  # pylint: disable=protected-access
    assert S.wave_bytes(row) == data


@pytest.mark.parametrize(
    "rec, step",
    [
        ([0x48, 0x20, 0x85, 0x04], S.PulseStep(0x840, 0x20, True, 5, 4)),
        ([0xFF, 0x10, 0x00, 0x00], S.PulseStep(None, 0x10, False, 0, 0)),
    ],
)
def test_pulse_steps(rec, step):
    assert S._pulse(rec) == step  # pylint: disable=protected-access
    assert S.pulse_bytes(step) == rec


def test_filter_steps_and_their_first_record():
    steps = (S.FilterStep(None, 2, 0x10, 1), S.FilterStep(0x80, 0xFF, 4, 4))
    table = S.FilterTable(steps)
    assert [S._filter(S.filter_bytes(s)) for s in steps] == list(
        steps
    )  # pylint: disable=protected-access
    assert (table.groove, table.voice) == ((0xFF, 2), 1)


TUNES = {
    "MUSICIANS/J/JCH/Guldkornekspressen_Intro.sid": (26, 19, 96),
    "MUSICIANS/P/Puterman/I_Could_Eat_a_Knob_at_Night.sid": (12, 5, 96),
    "MUSICIANS/J/JCH/Easy_Does_It.sid": (47, 31, 128),
    "MUSICIANS/D/DRAX/Acid.sid": (14, 16, 128),
    "MUSICIANS/S/Scorpio/Flexible.sid": (4, 0, 64),
}


def _no_bytes(value):
    if isinstance(value, (bytes, bytearray)):
        return False
    if dataclasses.is_dataclass(value):
        return all(_no_bytes(getattr(value, f.name)) for f in dataclasses.fields(value))
    if isinstance(value, dict):
        return all(_no_bytes(v) for v in value.values())
    if isinstance(value, (tuple, list)):
        return all(_no_bytes(v) for v in value)
    return True


@pytest.mark.parametrize("relpath", TUNES, ids=[p.split("/")[-1] for p in TUNES])
def test_real_songs_round_trip(relpath, hvsc):
    dec = decompile_sid(hvsc.read(relpath))
    song, data = S.decode(dec.layout)
    assert song == dec.song and _no_bytes(song)
    assert (len(song.sequences), len(song.instruments), len(song.pitch)) == TUNES[
        relpath
    ]
    assert S.encode(song) == data
    with pytest.raises(dataclasses.FrozenInstanceError):
        song.pitch = ()  # pylint: disable=assigning-non-slot


def test_the_plain_build_s_commands_are_its_slides_and_vibratos(hvsc):
    """Guldkorn's score runs eleven slides and two vibratos (the trackerprog's count)."""
    song = decompile_sid(hvsc.read("MUSICIANS/J/JCH/Guldkornekspressen_Intro.sid")).song
    used = {
        p.command
        for seq in song.sequences.values()
        for row in seq.rows
        for p in row.prefix
        if isinstance(p, S.RunCommand)
    }
    kinds = [type(song.commands[k]).__name__ for k in sorted(used)]
    assert (kinds.count("Slide"), kinds.count("Vibrato"), len(kinds)) == (11, 2, 13)
    assert song.filter.voice == 0
