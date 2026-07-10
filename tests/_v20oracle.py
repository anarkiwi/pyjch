"""py65 PSID register oracle for byte-exact V20 validation.

Thin wrapper over :func:`pysidtracker.oracle.register_grid`, which drives a
tune's ``init`` then ``play`` routine on a py65 6502 and samples the 25 SID
registers ``$D400..$D418`` at the end of each play call -- the ground-truth
per-frame grid the :class:`~pyjch.v20player.V20Player` output is checked
against.  ``py65`` is a required dependency (via pysidtracker).  pyjch's V20
replay uses no illegal opcodes, so ``illegal_opcodes`` stays at its default
(off).
"""

from pysidtracker.oracle import register_grid


def oracle_grid(raw, nframes):
    """Ground-truth py65 register grid for tune bytes ``raw``."""
    return register_grid(raw, nframes)
