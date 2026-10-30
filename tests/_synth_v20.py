"""Build a synthetic JCH NewPlayer V20 image for offline unit tests.

The bytes here are hand-authored (not from any copyrighted tune): a minimal V20
layout that carries every discovery idiom `pyjch.newplayer` /
`pyjch.v20player` scan for, plus coherent data tables (subtune, order lists,
patterns, instruments, wavetable columns, pitch/command tables) that let the
recovered model pass its coherence gate and the player run through fetch /
commit / instrument-init / per-frame-engine / portamento / vibrato / filter /
blit paths.  It is *not* an executable 6502 program (the code fragments exist
only for the byte-pattern discovery), so it is validated structurally, not
byte-exactly -- the byte-exact guarantee lives in the HVSC sidtrace oracle tests.
"""

from pysidtracker.header import write_psid

LOAD = 0x1000

# Absolute addresses of the data tables (chosen, all in image range).
SUBTUNE = 0x1800
PITCH = 0x1820
INSTRUMENTS = 0x1860
WAVE_NOTE = 0x1900
WAVE_CTRL = 0x1940
FILTERPROG = 0x1980
PWPROG = 0x19A0
PATLO = 0x19C0
PATHI = 0x19D0
CMDPARAM = 0x19E0
ORDER = (0x1A00, 0x1A10, 0x1A20)
PATTERNS = 0x1A40  # pattern N base = PATTERNS + N*0x10
IMAGE_LEN = 0x1B00
INIT_AT = 0x400  # image offset of the executable init routine


def _w(addr):
    return bytes((addr & 0xFF, (addr >> 8) & 0xFF))


def _fragments():
    """Discovery idiom fragments (concatenated, each padded with a NOP)."""
    frags = [
        b"\xa2\x00\xb9" + _w(SUBTUNE) + b"\x9d\x2e\x17",  # subtune base
        b"\xb9" + _w(PATLO) + b"\x85\xfb",  # patternptr lo
        b"\xb9" + _w(PATHI) + b"\x85\xfc",  # patternptr hi
        b"\xb9" + _w(WAVE_NOTE) + b"\xc9\x7e",  # wave-note column
        b"\xb9" + _w(PITCH + 1) + b"\x69\x00",  # pitch table (op = base+1)
        b"\xb9" + _w(INSTRUMENTS) + b"\xbc\x40\x17\x99\x05\xd4",  # instruments
        b"\xb9" + _w(WAVE_CTRL) + b"\x9d\x5d\x17",  # wave-ctrl column
        b"\xb9" + _w(FILTERPROG) + b"\x8d\x46\x17",  # filter program / groove
        b"\xbc\x81\x17\xb9" + _w(PWPROG + 3) + b"\x9d\x81\x17",  # PW program next
        b"\x0a\xa8\xb9" + _w(CMDPARAM) + b"\x48",  # command-param table
        b"\xad" + _w(CMDPARAM) + b"\x99\x05\xd4",  # gate-off AD re-arm cell
        b"\xad" + _w(CMDPARAM + 1) + b"\x99\x06\xd4",  # gate-off SR re-arm cell
        b"\xb9" + _w(INSTRUMENTS + 2) + b"\x48\x29\x80",  # inst wave-flags field
        b"\xb9" + _w(INSTRUMENTS + 3) + b"\x48\x29\xf0",  # inst filter field
        b"\xb9" + _w(INSTRUMENTS + 4) + b"\x8d\x90\x17",  # inst filter-prog field
        b"\xbc\x95\x17\xb9" + _w(WAVE_NOTE) + b"\x30",  # freq-note wave read
        b"\xa5\xfb\x48\xa5\xfc\x48",  # save the zero-page pointer
        b"\x68\x85\xfc\x68\x85\xfb\x60",  # restore it and return
        b"\xb9" + _w(FILTERPROG) + b"\xc9\xff\xf0\x03\x8d\x92\x17"
        b"\xb9" + _w(FILTERPROG + 2) + b"\x8d\x91\x17",  # filter-program start
        b"\xa9\x09\x99\x04\xd4",  # hard-restart CTRL immediate ($09)
    ]
    out = bytearray()
    for frag in frags:
        out += frag + b"\xea"  # NOP separator avoids cross-boundary matches
    return bytes(out)


def build_image(hard_restart=0x09):
    """Return ``(image_bytes, load)`` for a coherent synthetic V20 tune."""
    img = bytearray(IMAGE_LEN)
    img[0x000:0x006] = (
        b"\x4c" + _w(LOAD + INIT_AT) + b"\x4c\x09\x10"
    )  # JMP init ; JMP play
    img[0x006:0x009] = b"\x01\x01\x01"  # voice ACTIVE flags
    img[0x00A] = 0xF0  # RES/FILT accumulator seed
    img[0x020] = 0x00  # id-string flag = 0 -> skip optional init block
    frags = _fragments()
    img[0x030 : 0x030 + len(frags)] = frags
    if hard_restart != 0x09:
        # relocate the discovered hard-restart immediate
        idx = img.index(b"\xa9\x09\x99\x04\xd4")
        img[idx + 1] = hard_restart

    def put(addr, data):
        off = addr - LOAD
        img[off : off + len(data)] = data

    # subtune 0: order-list pointers per voice, tempo reload (>=2, fixed tempo).
    put(SUBTUNE, _w(ORDER[0]) + _w(ORDER[1]) + _w(ORDER[2]) + b"\x03\x00")
    # pitch table: a small ascending 16-bit table (values are arbitrary).
    put(PITCH, bytes((i & 0xFF) for i in range(0x40)))
    # instruments (8 bytes each): AD,SR,wavspd/flags,filt,fprog,pwstart,ws,ws2.
    put(INSTRUMENTS + 0 * 8, b"\x22\x33\x01\x11\x00\x00\x00\x00")  # note+filter
    put(INSTRUMENTS + 1 * 8, b"\x21\x30\x41\x08\x00\x02\x00\x00")  # abs+fprog
    put(INSTRUMENTS + 2 * 8, b"\x10\x20\x02\x00\x00\x04\x02\x03")  # plain
    put(INSTRUMENTS + 3 * 8, b"\x0a\x0a\x80\x00\x00\x00\x01\x01")  # ws2!=ws
    # wavetable columns: note col ($7E hold, $7F jump) + parallel ctrl col.
    put(WAVE_NOTE, b"\x0c\x10\x7e\x14\x7f\x00\x08\x90\x00\x00")
    put(WAVE_CTRL, b"\x41\x21\x11\x81\x02\x40\x40\x09\x00\x00")
    # filter program (value, step, dwell, next columns interleaved).
    put(FILTERPROG, b"\x00\x02\x01\x04\xa0\xff\x01\x08\x30\x02\x01\x00")
    # PW program (reset, step, dir+rate, next columns interleaved).
    put(PWPROG, b"\x08\x08\x03\x04\x02\x30\x10\x00\xff\x10\x84\x00")
    # command-param table (lo,hi) x commands: porta/vibrato/tempo/vol/AD/patch.
    put(
        CMDPARAM,
        b"\x0f\x00"  # cmd0 unused
        b"\x10\x05"  # cmd1: porta (lo&$F0=$10<$30)
        b"\x60\x34"  # cmd2: vibrato ($60)
        b"\xe0\x04"  # cmd3: tempo ($E0)
        b"\xf0\x1f"  # cmd4: volume ($F0)
        b"\x90\x22"  # cmd5: AD override ($90)
        b"\x50\x03",  # cmd6: instrument patch (else)
    )
    # pattern pointer tables: patterns 0..4.
    put(PATLO, bytes((PATTERNS + n * 0x10) & 0xFF for n in range(8)))
    put(PATHI, bytes(((PATTERNS + n * 0x10) >> 8) & 0xFF for n in range(8)))
    # patterns (each ends with $7F): commands + notes + rest + hold.
    put(PATTERNS + 0 * 0x10, b"\xc1\x0c\x7f")  # porta cmd, note, end
    put(PATTERNS + 1 * 0x10, b"\xc2\x10\x7f")  # vibrato cmd, note, end
    put(PATTERNS + 2 * 0x10, b"\xa1\x14\x00\x7f")  # select inst1(abs), note, rest
    put(PATTERNS + 3 * 0x10, b"\x7e\x7f")  # hold, end
    put(PATTERNS + 4 * 0x10, b"\x83\xc3\xc4\xc5\xc6\x14\x7f")  # dur/tempo/vol/AD/patch
    # order lists: voice0 walks several patterns; voice1 has a transpose prefix.
    put(ORDER[0], b"\x00\x01\x04\xff")
    put(ORDER[1], b"\x85\x02\xff")  # $85 = transpose prefix
    put(ORDER[2], b"\x03\xff")
    put(LOAD + INIT_AT, _init_routine(img))
    return bytes(img), LOAD


def _init_routine(img):
    """A 6502 init (straight-line stores, RTS) that seeds the work cells.

    Seeds what the V20 init does for this image: the per-voice order pointers
    and their restart copies from the subtune record, the tempo, the groove
    counters and the volume, and clears the SID registers and voice state.
    """
    record = SUBTUNE - LOAD
    stores = {}
    for voice in range(3):
        lo, hi = img[record + voice * 2], img[record + voice * 2 + 1]
        for base, value in ((0x72E, lo), (0x731, hi), (0x734, lo), (0x737, hi)):
            stores[LOAD + base + voice] = value
    stores[LOAD + 0x747] = img[record + 6]
    stores.update({0xD400 + reg: 0 for reg in range(0x17)})
    stores.update({LOAD + 0x14 + n: 0 for n in range(0x0C)})
    stores.update({LOAD + 0x748 + n: 0 for n in range(0x15)})
    stores.update({LOAD + 0x794: 1, LOAD + 0x746: 3, LOAD + 0x009: 0x0F})
    code = bytearray()
    for addr, value in stores.items():
        code += bytes((0xA9, value, 0x8D)) + _w(addr)
    return bytes(code) + b"\x60"


def build_psid(hard_restart=0x09, songs=1, start=1):
    """Wrap the synthetic image in a minimal PSID v2 container."""
    image, load = build_image(hard_restart)
    return write_psid(
        load=load,
        init=0x1000,
        play=0x1003,
        image=image,
        name="synth v20",
        songs=songs,
        start_song=start,
    )
