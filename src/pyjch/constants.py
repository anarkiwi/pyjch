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

# SID register map.
SID_REGISTERS = 25
VOICES = 3
VOICE_REG_SIZE = 7

# Per-voice SID base offsets ($D400 + offset).
SID_OFFSET = (0, 7, 14)

# Pulse-width high registers carry only their low nibble (12-bit pulse).
PW_HI_REGS = (0x03, 0x0A, 0x11)

# Per-tune operand offsets-from-load.  The player binary is identical
# across JCH tunes, so each per-tune immediate / pointer-table base lives
# at a fixed offset in the code; the reader reads the byte (or 16-bit
# operand) there to discover the per-tune value (relocation-safe).
OP_INIT_AD = 0x0AA  # LDA #imm @ $10AA -> attack/decay default
OP_INIT_SR = 0x0B5  # LDA #imm @ $10B5 -> sustain/release default
OP_GATEOFF_CTRL = 0x199  # LDA #imm @ $1198 -> gate-off / rest CTRL
OP_GATE_CTRL = 0x1E1  # LDA #imm @ $11E0 -> gate-on CTRL
OP_SUBPTR_LO = 0x12C  # LDA abs,Y operand @ $112B -> subpattern ptr lo base
OP_SUBPTR_HI = 0x131  # LDA abs,Y operand @ $1130 -> subpattern ptr hi base

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

# C64 timing.  A PAL frame is 312 rasterlines x 63 cycles.
PAL_CLOCK_HZ = 985248
PAL_CYCLES_PER_FRAME = 19656
NTSC_CLOCK_HZ = 1022727
NTSC_CYCLES_PER_FRAME = 17095

# Standard JCH NewPlayer entry points (PSID header normally matches).
DEFAULT_LOAD = 0x1000
DEFAULT_INIT = 0x1060
DEFAULT_PLAY = 0x10E8
