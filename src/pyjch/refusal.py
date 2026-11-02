"""Why the V20 spec cannot play a tune the V20 code build plays (the tune goes to EmuPlayer).

The spec keeps the player's variables as state with no address, so a tune whose song data
can reach one as memory is refused; the reasons are listed in ``REASONS``.
"""

from typing import Optional, Set

from pyjch.constants import (
    OVERLAP,
    PITCH_REACH,
    SONG_UNPARSED,
    SHARED_BYTES,
    TEMPORARIES,
    VOICE_CELLS,
    VOICE_BYTES,
    VOICE_REGISTERS,
    VOICE_WORDS,
)
from pyjch.errors import SidParseError
from pyjch.extract import extract
from pyjch.songmodel import CommandKind, NoteEvent, NoteKind, PatternEvent

NOTES = 96
PITCH_BYTES = 2 * NOTES
WAVE_HOLD = 0x7E
WAVE_JUMP = 0x7F
PATTERN_END = 0x7F


def work_bytes(base: int) -> Set[int]:
    """The addresses of the work area at ``base`` the player writes."""
    offsets = set(SHARED_BYTES.values()) | set(TEMPORARIES)
    for voice in range(3):
        offsets.update(off + voice for off in VOICE_BYTES.values())
        offsets.update(off + voice for pair in VOICE_WORDS.values() for off in pair)
    return {(base + off) & 0xFFFF for off in offsets}


def data_bytes(bases) -> Set[int]:
    """The addresses of every table the spec reads or patches, to the extent an index reaches."""
    extents = (
        (bases.pitch, PITCH_BYTES),
        (bases.wave_note, 256),
        (bases.wave_ctrl, 256),
        (bases.filterprog, 256),
        (bases.pwprog, 256),
        (bases.instruments, 256),
        (bases.patternptr_lo, 256),
        (bases.patternptr_hi, 256),
        (bases.cmdparam, 128),
        (bases.rearm_ad, 1),
        (bases.rearm_sr, 1),
    )
    return {(at + k) & 0xFFFF for at, size in extents for k in range(size)}


def _image_byte(model, addr: int) -> int:
    at = addr - model.load_addr
    return model.image[at] if 0 <= at < len(model.image) else 0


def _voice_cells(model) -> bool:
    """Whether each voice's register offset and filter masks are the spec's."""
    at = model.init_addr + VOICE_REGISTERS
    return all(
        (
            _image_byte(model, at + v),
            _image_byte(model, at - 6 + v),
            _image_byte(model, at - 3 + v),
        )
        == (7 * v, 1 << v, 0xFF ^ (1 << v))
        for v in range(3)
    )


def _bit(byte: int) -> int:
    return 1 << (byte if byte < 0x80 else 128 + (byte & 0x7F))


def _program(model, bases, start: int, steps: int) -> int:
    """The notes a wave program started at row ``start`` adds in its first ``steps`` rows, as a
    bit set (bit ``a`` for a relative note, ``128 + a`` for an absolute one)."""
    found, row = 0, start
    for _ in range(steps + 1):
        byte = _image_byte(model, bases.wave_note + row)
        if byte == WAVE_HOLD:
            return found | _bit(_image_byte(model, bases.wave_note + (row - 1) % 256))
        if byte == WAVE_JUMP:
            row = _image_byte(model, bases.wave_ctrl + row)
            byte = _image_byte(model, bases.wave_note + row)
        found |= _bit(byte)
        row = (row + 1) % 256
    return found


def _past(rows: int, shift: int) -> bool:
    """Whether a note of the set ``rows`` lands past the table: a relative one with ``shift``
    added, an absolute one as it is."""
    relative, absolute = rows & (1 << 128) - 1, rows >> 128
    window = ((1 << 128 - NOTES) - 1) << NOTES
    turn = (128 - shift % 128) % 128
    low = (window << turn | window >> 128 - turn) & (1 << 128) - 1
    return bool(relative & low or absolute & window)


def _patched(tune) -> dict:
    """The wave starts the song's patch commands give each instrument."""
    out: dict = {}
    for events in tune.patterns:
        for event in events:
            cmd = event.command
            if cmd is not None and cmd.kind is CommandKind.WAVE_PATCH:
                if cmd.param_lo is not None and cmd.param_hi is not None:
                    out.setdefault(cmd.param_lo & 0x1F, set()).add(cmd.param_hi)
    return out


def _beats(model, tune) -> int:
    """The most ticks a row can last: the largest tempo or groove entry, plus one."""
    tempos = {sub.tempo for sub in tune.subtunes}
    tempos.add(_image_byte(model, model.init_addr + SHARED_BYTES["tempo"]))
    tempos.update(c.hi for c in tune.commands if c.kind is CommandKind.TEMPO)
    if any(t < 2 for t in tempos):
        tempos.update(tune.groove)
    return max(tempos) + 1


def _rows(tune, order, note: int, shift: int, inst: int):
    """``(note, transpose, instrument, ticks)`` for each row of the order list ``order``: the note
    and transpose it sounds, the instrument whose program runs (None: the one the init left) and
    the ticks since it started; walked twice so what the restart carries over is seen.
    """
    dur, since, begun, tie = 0, 0, None, False
    for _ in range(2):
        for entry in order.entries:
            if entry.transpose >= 0x80:
                shift = entry.transpose & 0x7F
            if entry.pattern >= len(tune.patterns):
                raise LookupError(entry.pattern)
            events = list(tune.patterns[entry.pattern])
            if not events or events[-1].command is not None:
                events.append(
                    PatternEvent(
                        PATTERN_END, note=NoteEvent(NoteKind.NOTE, PATTERN_END)
                    )
                )
            for event in events:
                cmd = event.command
                if cmd is None:
                    if event.note.kind is NoteKind.NOTE:
                        note = event.note.value
                        if not tie:
                            since, begun = 0, inst
                    since += dur + 1
                    yield note, shift, begun, since
                    tie = False
                elif cmd.kind is CommandKind.INSTRUMENT:
                    inst = cmd.arg
                elif cmd.kind is CommandKind.DURATION:
                    dur, tie = cmd.arg, bool(cmd.delay)


def _pitch_reach(model, bases, tune) -> bool:
    base = model.init_addr
    voices = range(3)

    def initial(name: str) -> list:
        return [_image_byte(model, base + VOICE_BYTES[name] + v) for v in voices]

    patched = _patched(tune)
    beats = _beats(model, tune)

    def program(inst, ticks: int, voice: int) -> int:
        starts, dwell = {initial("wtptr")[voice]}, 0
        if inst is not None:
            record = bases.instruments + 8 * inst
            starts = set(patched.get(inst, ()))
            starts |= {_image_byte(model, record + 6), _image_byte(model, record + 7)}
            dwell = _image_byte(model, record + 2) & 0x0F
        steps = ticks * beats // (dwell + 1) + 2
        found = 0
        for start in starts:
            found |= _program(model, bases, start, steps)
        return found

    if any(_past(_program(model, bases, start, 3), 0) for start in initial("wtptr")):
        return True
    vibrato = any(c.kind is CommandKind.VIBRATO for c in tune.commands)
    notes, trans, insts = initial("s_note"), initial("s_trans"), initial("s_inst")
    for sub in tune.subtunes:
        for voice, order in enumerate(sub.order_lists):
            first = notes[voice], trans[voice] >> 1, insts[voice] >> 3
            try:
                for note, shift, inst, ticks in _rows(tune, order, *first):
                    if vibrato and note & 0x7F >= NOTES - 1:
                        return True
                    if _past(program(inst, ticks, voice), note + shift):
                        return True
            except LookupError:
                return True
    return False


def refusal(model, bases) -> Optional[str]:
    """The reason the V20 spec cannot play ``model`` (whose code build is ``bases``), or None."""
    if not _voice_cells(model):
        return VOICE_CELLS
    if work_bytes(model.init_addr) & data_bytes(bases):
        return OVERLAP
    try:
        tune = extract(model, variant=False)
    except SidParseError:
        return SONG_UNPARSED
    return PITCH_REACH if _pitch_reach(model, bases, tune) else None
