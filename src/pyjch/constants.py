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

SID_REGISTERS = SID_REG_COUNT  # $D400..$D418 (25 registers)
VOICES = _registers.SID_VOICES
VOICE_REG_SIZE = _registers.SID_VOICE_OFFSET[1]  # per-voice register stride (7)

# Per-voice SID base offsets ($D400 + offset).
SID_OFFSET = SID_VOICE_OFFSET

# Per-tune immediates and pointer-table bases live in the player code as
# instruction operands.  The reader locates them by their surrounding
# instruction bytes (a ``pysidtracker`` ``CodePattern`` masked search) rather
# than by a fixed
# offset, so discovery survives both relocation and the small per-tune code
# shifts a fixed offset cannot.  For reference, the canonical (load $1000,
# Flexible/Simple_Tune) offsets are: AD @+0xAA, SR @+0xB5, gate-off @+0x199,
# gate-on @+0x1E1, subptr-lo @+0x12C, subptr-hi @+0x131.

# Fixed in-image work / data addresses the player reads (absolute, relative
# to load; the player binary places them at the same offsets across tunes).
ORDERLIST_PTR_TABLE = 0x010  # $1010: per-subtune orderlist ptr pairs (8/subtune)
FREQ_LO = 0x21F  # $121F: note frequency table, low bytes (0x80 entries)
FREQ_HI = 0x220  # $1220: note frequency table, high bytes
FREQ_TABLE_LEN = 0x80

# Per-voice transpose / detune immediates the play routine indexes by X.
VOICE_TRANSPOSE = 0x00A  # $100A,X : per-voice arp/transpose base byte
VOICE_DETUNE = 0x00D  # $100D,X : per-voice frequency add (detune)

# Player work-RAM addresses (the play routine's $..,X arrays).  Init writes
# only a handful; the rest keep their loaded-image value.
WORK_CUR_PTR_LO = 0x2DF  # $12DF,X : current opcode-stream pointer, low
WORK_CUR_PTR_HI = 0x2E2  # $12E2,X : current opcode-stream pointer, high
WORK_BASE_PTR_LO = 0x2E5  # $12E5,X : orderlist base pointer, low
WORK_BASE_PTR_HI = 0x2E8  # $12E8,X : orderlist base pointer, high
WORK_SAVE_PTR_LO = 0x2EB  # $12EB,X : saved (pushed) pointer, low
WORK_SAVE_PTR_HI = 0x2EE  # $12EE,X : saved (pushed) pointer, high
WORK_SID_OFFSET = 0x2F1  # $12F1,X : SID voice register offset (0,7,14)
WORK_TEMPO = 0x2F4  # $12F4 / $12F5 : tempo counter / reload
WORK_GATE_CTR = 0x2F9  # $12F9,X : per-voice gate-delay counter
WORK_GATE_RELOAD = 0x2F6  # $12F6,X : per-voice gate-delay reload
WORK_GATE_FLAG = 0x2FC  # $12FC,X : gate-this-frame flag
WORK_FREQ_LO = 0x2FF  # $12FF,X : computed FREQ_LO
WORK_FREQ_HI = 0x302  # $1302,X : computed FREQ_HI
WORK_TRANSPOSE = 0x305  # $1305,X : current transpose offset
WORK_SUBPAT_DEPTH = 0x308  # $1308,X : in-subpattern flag/depth

# Default subtune orderlist-pointer index (the 7th pair feeds the tempo).
TEMPO_PTR_INDEX = 0x2F4

# Zero-page opcode-stream pointer used by the play routine.
ZP_PTR_LO = 0xFB
ZP_PTR_HI = 0xFC

# SID register init values written by the JCH init routine.
INIT_CTRL = 0x88  # $D404/$D40B/$D412
INIT_PW_HI_V0 = 0x04  # $D403
INIT_PW_HI_V12 = 0x01  # $D40A / $D411
INIT_VOLUME = 0x0F  # $D418

# C64 frame timing (documented hardware facts, shared).
PAL_CLOCK_HZ = _registers.PAL_CLOCK_HZ
PAL_CYCLES_PER_FRAME = _registers.PAL_CYCLES_PER_FRAME
NTSC_CLOCK_HZ = _registers.NTSC_CLOCK_HZ
NTSC_CYCLES_PER_FRAME = _registers.NTSC_CYCLES_PER_FRAME

# Standard JCH NewPlayer entry points (PSID header normally matches).
DEFAULT_LOAD = 0x1000
DEFAULT_INIT = 0x1060
DEFAULT_PLAY = 0x10E8
