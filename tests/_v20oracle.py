"""Clean-room py65 PSID register oracle for byte-exact V20 validation.

Drives a tune's ``init`` then ``play`` routine on a py65 6502 and samples the
25 SID registers ``$D400..$D418`` at the end of each play call -- the
ground-truth per-frame grid the :class:`~pyjch.v20player.V20Player` output is
checked against.  No player source is vendored; py65 is a clean-room 6502 and
this harness is original.  ``py65`` is a test-only dependency; callers skip when
it (or the tune) is unavailable.
"""

import struct

try:
    from py65.devices.mpu6502 import MPU
    from py65.memory import ObservableMemory

    HAVE_PY65 = True
except ImportError:  # pragma: no cover - environment without py65
    HAVE_PY65 = False

SID_BASE = 0xD400
NREG = 25


def parse_psid(raw):
    """Return ``(load, init, play, startsong, image)`` from PSID/RSID bytes."""
    if raw[:4] not in (b"PSID", b"RSID"):
        raise ValueError("not a PSID/RSID")
    _ver, doff, load, init, play, _songs, start = struct.unpack(">HHHHHHH", raw[4:18])
    body = raw[doff:]
    if load == 0:
        load = body[0] | (body[1] << 8)
        body = body[2:]
    return load, init or load, play or load, start, body


class Oracle:
    """py65-driven register oracle for one tune."""

    def __init__(self, raw):
        self.load, self.init, self.play, self.start, body = parse_psid(raw)
        self.mem = ObservableMemory()
        for i, byte in enumerate(body):
            self.mem[self.load + i] = byte
        self.mpu = MPU(memory=self.mem)
        # Minimal VIC/SID read model so init raster-sync / SID-detect loops in
        # some tunes terminate (the harness has no real VIC).
        self.mem.subscribe_to_read([0xD011, 0xD012], self._raster)
        self.mem.subscribe_to_read([0xD41B, 0xD41C], self._sidread)

    def _raster(self, addr):
        line = (self.mpu.processorCycles // 63) % 312
        if addr == 0xD012:
            return line & 0xFF
        return (self.mem[0xD011] & 0x7F) | (((line >> 8) & 1) << 7)

    def _sidread(self, addr):  # pylint: disable=unused-argument
        return (self.mpu.processorCycles >> 3) & 0xFF

    def _run(self, pc, a=0, max_steps=2_000_000):
        m = self.mpu
        m.sp = 0xFD
        m.memory[0x01FF] = 0x00
        m.memory[0x01FE] = 0xFF  # RTS -> $0100 sentinel
        m.pc, m.a, m.x, m.y = pc, a, 0, 0
        for _ in range(max_steps):
            if m.pc == 0x0100:
                return
            m.step()
        raise RuntimeError("py65 step budget exceeded")

    def grid(self, nframes):
        """Run ``init`` then ``nframes`` play calls; return per-frame reg grids."""
        self._run(self.init, self.start - 1)
        rows = []
        for _ in range(nframes):
            self._run(self.play)
            rows.append([self.mem[SID_BASE + i] for i in range(NREG)])
        return rows


def oracle_grid(raw, nframes):
    """Ground-truth grid for tune bytes ``raw``, or ``None`` if py65 is absent."""
    if not HAVE_PY65:
        return None
    return Oracle(raw).grid(nframes)
