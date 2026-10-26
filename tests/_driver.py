"""Synthetic editor-work-file driver prefixes (headerless, loaded at ``$0F00``)."""

from pyjch.editor import NP25_PROFILE

# NP25-like (NP22/23/25 data region; pitch slot roomy enough for 256 bytes) and NP20-like (shifted bases) layouts.
NP25_BASES = {
    "init": 0x18E4, "pitch": 0x1700, "finetune": 0x1800, "wave": 0x1900,
    "wave2": 0x1A00, "filter": 0x1B00, "pulse": 0x1C00, "inst": 0x1D00,
    "seqlo": 0x1E00, "seqhi": 0x1F00, "cmd": 0x2000,
    "order0": 0x2100, "order1": 0x2500, "order2": 0x2900,
}  # fmt: skip
NP20_BASES = {
    "init": 0x17CB, "pitch": 0x166D, "finetune": 0x1743, "wave": 0x18CB,
    "wave2": 0x19CB, "filter": 0x1ACB, "pulse": 0x1BCB, "inst": 0x1CCB,
    "seqlo": 0x1DCB, "seqhi": 0x1ECB, "cmd": 0x1FCB,
    "order0": 0x20CB, "order1": 0x24CB, "order2": 0x28CB,
}  # fmt: skip


def make_driver(bases=None, profile=NP25_PROFILE, code=0xEA):
    """Driver image: filler "code" up to the first table, with a ``$0FA0`` block."""
    bases = bases or NP25_BASES
    idx = {
        "init": profile.idx_init, "pitch": profile.idx_pitch,
        "finetune": profile.idx_finetune, "wave": profile.idx_wave,
        "wave2": profile.idx_wave2, "filter": profile.idx_filter,
        "pulse": profile.idx_pulse, "inst": profile.idx_inst,
        "order0": profile.idx_order[0], "order1": profile.idx_order[1],
        "order2": profile.idx_order[2], "seqlo": profile.idx_seqlo,
        "seqhi": profile.idx_seqhi, "cmd": profile.idx_cmd,
    }  # fmt: skip
    img = bytearray([code]) * (min(bases.values()) - profile.load_addr)
    for name, index in idx.items():
        off = profile.ptr_addr(index) - profile.load_addr
        img[off : off + 2] = bytes([bases[name] & 0xFF, bases[name] >> 8])
    return bytes(img)
