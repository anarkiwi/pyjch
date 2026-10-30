"""The JchPlayer framed into a base register log has the expected clocking."""

import pytest
from pysidtracker import (
    DEFAULT_WRITE_SPACING,
    PAL_CYCLES_PER_FRAME,
    RegWrite,
    read_reglog,
    register_writes_from_player,
    write_reglog,
)

from pyjch import reader
from pyjch.player import JchPlayer
from tests.tunes import V0X_REFERENCES


@pytest.mark.parametrize("relpath", V0X_REFERENCES)
def test_register_writes_clocking(relpath, hvsc):
    player = JchPlayer(reader.read(hvsc(relpath)))
    writes = list(register_writes_from_player(player, max_frames=4))
    assert writes and all(isinstance(w, RegWrite) for w in writes)
    init = [w for w in writes if w.clock < PAL_CYCLES_PER_FRAME]
    assert len(init) == 25
    assert init[0].clock == 0 and init[1].clock == DEFAULT_WRITE_SPACING
    first_play = min(w.clock for w in writes if w.clock >= PAL_CYCLES_PER_FRAME)
    assert first_play == PAL_CYCLES_PER_FRAME


def test_a_written_reglog_reads_back_as_the_writes_it_holds(tmp_path):
    writes = [RegWrite(0, 0, 0x12), RegWrite(40, 4, 0x41), RegWrite(19656, 1, 0x34)]
    path = tmp_path / "log.txt"
    write_reglog(writes, path)
    assert list(read_reglog(path)) == writes
