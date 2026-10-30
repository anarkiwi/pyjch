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
FREQ_LO = 0x21F  # $121F: note frequency table, low bytes (0x80 entries)
FREQ_HI = 0x220  # $1220: note frequency table, high bytes
FREQ_TABLE_LEN = 0x80

# C64 frame timing (documented hardware facts, shared).
PAL_CLOCK_HZ = _registers.PAL_CLOCK_HZ
PAL_CYCLES_PER_FRAME = _registers.PAL_CYCLES_PER_FRAME
NTSC_CLOCK_HZ = _registers.NTSC_CLOCK_HZ
NTSC_CYCLES_PER_FRAME = _registers.NTSC_CYCLES_PER_FRAME

# Standard JCH NewPlayer entry points (PSID header normally matches).
DEFAULT_LOAD = 0x1000
DEFAULT_INIT = 0x1060
DEFAULT_PLAY = 0x10E8
