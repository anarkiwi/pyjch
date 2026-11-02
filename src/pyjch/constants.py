"""Constants for the JCH NewPlayer and song format.

Values follow the reverse-engineered JCH NewPlayer
(``re-trackers/JCH_NewPlayer/{decompile.c,disasm.asm}``), cross-checked
byte-exact against the ``preframr-sidtrace`` register oracle.  The player
code is identical across JCH tunes; only its DATA (orderlists, subpatterns,
instruments, frequency tables and the addresses they live at) differs per
tune, so the reader DISCOVERS each per-tune immediate and table base from
the player code's 16-bit operands (relocation-safe -- no fixed-address
assumption).
"""

# SID register map -- hardware facts shared via ``pysidtracker.registers``.
from pysidtracker import registers as _registers

SID_BASE = _registers.SID_BASE
SID_REG_COUNT = _registers.SID_REG_COUNT
SID_VOICE_OFFSET = _registers.SID_VOICE_OFFSET
PW_HI_REGS = _registers.PW_HI_REGS

VOICES = _registers.SID_VOICES

# Per-tune immediates and pointer-table bases live in the player code as
# instruction operands.  The reader locates them by their surrounding
# instruction bytes (a ``pysidtracker`` ``CodePattern`` masked search) rather
# than by a fixed
# offset, so discovery survives both relocation and the small per-tune code
# shifts a fixed offset cannot.  For reference, the canonical (load $1000,
# Flexible/Simple_Tune) offsets are: AD @+0xAA, SR @+0xB5, gate-off @+0x199,
# gate-on @+0x1E1, subptr-lo @+0x12C, subptr-hi @+0x131.

# Fixed in-image data addresses the reader recovers (relative to load; the player
# binary places them at the same offsets across tunes).  The player's work cells
# are bound in ``specs/v0x.play``.
ORDERLIST_PTR_TABLE = 0x010  # $1010: per-subtune orderlist ptr pairs (8/subtune)
FREQ_LO = 0x21F  # $121F: note frequency table, interleaved 16-bit entries (low bytes)
FREQ_HI = 0x220  # $1220: the same entries' high bytes
FREQ_TABLE_LEN = 0x60  # notes before the table runs into the work cells at $12DF

# C64 frame timing (documented hardware facts, shared).
PAL_CLOCK_HZ = _registers.PAL_CLOCK_HZ
PAL_CYCLES_PER_FRAME = _registers.PAL_CYCLES_PER_FRAME
NTSC_CLOCK_HZ = _registers.NTSC_CLOCK_HZ
NTSC_CYCLES_PER_FRAME = _registers.NTSC_CYCLES_PER_FRAME

# Standard JCH NewPlayer entry points (PSID header normally matches).
DEFAULT_LOAD = 0x1000
DEFAULT_INIT = 0x1060
DEFAULT_PLAY = 0x10E8

# The V20 player's variables lie in a work area at fixed offsets from the code base (the init
# address).  Offsets of the play variables a voice owns (one byte per voice, three voices apart), of the
# words made of a low-byte and a high-byte table, and of the shared bytes.
VOICE_BYTES = {
    "active": 0x006,
    "note": 0x014,
    "trans": 0x017,
    "gate": 0x01A,
    "inst": 0x01D,
    "detune": 0x743,
    "tie": 0x748,
    "vib_on": 0x74B,
    "porta_on": 0x74E,
    "patpos": 0x751,
    "rearming": 0x754,
    "length": 0x757,
    "remaining": 0x75A,
    "ctrl": 0x75D,
    "ported": 0x760,
    "vibed": 0x763,
    "quiet": 0x766,
    "vibwait": 0x769,
    "vibperiod": 0x76C,
    "vibrate": 0x76F,
    "vibbacc": 0x772,
    "vibdir": 0x775,
    "vibdep": 0x77E,
    "pwcur": 0x781,
    "pwdw": 0x784,
    "pwdir": 0x78D,
    "wtptr": 0x795,
    "wtdw": 0x798,
    "wtdwrel": 0x79B,
    "portadir": 0x7A4,
    "s_trans": 0x7AD,
    "s_inst": 0x7B0,
    "s_note": 0x7B3,
    "s_porta": 0x7B6,
    "s_vib": 0x7B9,
    "s_gate": 0x7BC,
    "s_sr": 0x7C5,
    "ad": 0x7BF,
    "sr": 0x7C2,
}
VOICE_WORDS = {
    "freq": (0x00C, 0x00F),
    "start": (0x72E, 0x731),
    "rst": (0x734, 0x737),
    "vibacc": (0x778, 0x77B),
    "pw": (0x787, 0x78A),
    "portaslide": (0x79E, 0x7A1),
    "portaacc": (0x7A7, 0x7AA),
}
SHARED_BYTES = {
    "vol": 0x009,
    "resfilt": 0x00A,
    "scratch": 0x00B,
    "delay": 0x7CA,
    "tempo": 0x747,
    "groove_ctr": 0x746,
    "groove_pos": 0x794,
    "fpos": 0x790,
    "fdwell": 0x791,
    "cutoff": 0x792,
    "fmode": 0x793,
}
# Work-area bytes the 6502 player writes that the spec leaves out: the vibrato's temporary.
TEMPORARIES = (0x72D,)
# The V0x player's play variables, offsets from the load address.
V0X_BYTES = {
    "left": 0x2F9,
    "beats": 0x2F6,
    "transpose": 0x305,
    "inside": 0x308,
    "shift": 0x00A,
    "detune": 0x00D,
}
V0X_WORDS = {"freq": (0x2FF, 0x302), "base": (0x2E5, 0x2E8), "cur": (0x2DF, 0x2E2)}
V0X_SHARED = {"tempo": 0x2F4, "speed": 0x2F5}
V0X_VOICE_REGISTERS = 0x2F1
# The play variables the spec keeps scaled down from the byte the 6502 holds: an instrument and
# a pulse direction are held as a table offset or a flag bit, a transpose in half steps.
VARIABLE_SHIFTS = {
    "inst": 3,
    "s_inst": 3,
    "trans": 1,
    "s_trans": 1,
    "pwdir": 7,
    "portadir": 5,
}
# Where each voice's SID registers start: the spec's records place them seven apart.
VOICE_REGISTERS = 0x740


# Why the V20 spec cannot play a V20 code build (see pyjch.refusal).
VOICE_CELLS = "voice-registers"
OVERLAP = "variables-over-data"
PITCH_REACH = "pitch-past-table"
SONG_UNPARSED = "song-unparsed"
SPEC_REFUSALS = (VOICE_CELLS, OVERLAP, PITCH_REACH, SONG_UNPARSED)
