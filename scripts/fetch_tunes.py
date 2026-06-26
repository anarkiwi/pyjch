#!/usr/bin/env python3
"""Download JCH NewPlayer ``.sid`` test tunes into a gitignored cache.

These tunes are HVSC copyright works and are **never** committed to this
repo (see ``.gitignore``).  They are fetched on demand from a public HVSC
mirror into ``tests/.tunecache/`` (gitignored), so a fresh clone works with
no machine-specific paths.  The byte-exact player tests are skipped when a
tune is absent (and CI has no network), exactly mirroring how the
register-log oracle test is env-gated.

Usage::

    python scripts/fetch_tunes.py                 # fetch every test tune
    python scripts/fetch_tunes.py --id flexible   # fetch one
    python scripts/fetch_tunes.py --list          # print id -> HVSC path

Programmatic::

    from scripts.fetch_tunes import fetch, TUNES
    sid_path = fetch(TUNES["flexible"])
"""

from __future__ import annotations

import argparse
import os
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CACHE = Path(os.environ.get("JCH_TUNECACHE", str(REPO / "tests" / ".tunecache")))

# Public HVSC mirror.  Override with ``$HVSC_MIRROR``.  The relative HVSC
# path is appended verbatim.
MIRROR = os.environ.get("HVSC_MIRROR", "https://hvsc.brona.dk/HVSC/C64Music").rstrip(
    "/"
)

# id -> HVSC relative path.  Both tunes are the JCH NewPlayer byte-exact
# validation references (load $1000, init->$1060, play->$10E8).
TUNES = {
    "flexible": "MUSICIANS/S/Scorpio/Flexible.sid",
    "simple": "MUSICIANS/J/JCH/Simple_Tune.sid",
}


def _is_sid(data: bytes) -> bool:
    return data[:4] in (b"PSID", b"RSID")


def fetch(relpath: str, *, force: bool = False) -> Path:
    """Fetch ``relpath`` from the HVSC mirror into the cache; return its path."""
    relpath = relpath.lstrip("/")
    dest = CACHE / relpath
    if dest.exists() and not force:
        return dest
    url = f"{MIRROR}/{relpath}"
    req = urllib.request.Request(url, headers={"User-Agent": "pyjch/fetch_tunes"})
    with urllib.request.urlopen(req, timeout=60) as resp:  # nosec B310 (https mirror)
        data = resp.read()
    if not _is_sid(data):
        raise RuntimeError(f"{url}: not a SID file (magic {data[:4]!r})")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return dest


def fetch_id(tune_id: str, *, force: bool = False) -> Path:
    """Fetch the tune registered under ``tune_id``."""
    return fetch(TUNES[tune_id], force=force)


def main(argv=None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--id", help="only this tune id")
    parser.add_argument("--force", action="store_true", help="re-download")
    parser.add_argument("--list", action="store_true", help="print id -> path")
    args = parser.parse_args(argv)

    if args.list:
        for tid, rel in TUNES.items():
            print(f"{tid}\t{rel}")
        return 0

    ids = [args.id] if args.id else list(TUNES)
    for tid in ids:
        path = fetch_id(tid, force=args.force)
        print(f"{tid}: {TUNES[tid]} -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
