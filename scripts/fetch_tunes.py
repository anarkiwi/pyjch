#!/usr/bin/env python3
"""Download JCH NewPlayer ``.sid`` test tunes into a gitignored cache.

These tunes are HVSC copyright works and are **never** committed to this
repo (see ``.gitignore``).  They are fetched from a public HVSC mirror into
``tests/.tunecache/`` (gitignored), so a fresh clone works with no
machine-specific paths.  CI runs ``--corpus`` to populate the cache (persisted
across runs by ``actions/cache``) so the corpus reader tests and the V20 py65
byte-exact oracle tests run for real; an individual tune is skipped only when it
is genuinely unreachable after retries.

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
import importlib
import os
import sys
from pathlib import Path

from pysidtracker.testing import DEFAULT_MIRROR, fetch_tune

REPO = Path(__file__).resolve().parent.parent
CACHE = Path(os.environ.get("JCH_TUNECACHE", str(REPO / "tests" / ".tunecache")))

# Public HVSC mirror.  Override with ``$HVSC_MIRROR``.  The relative HVSC
# path is appended verbatim.
MIRROR = os.environ.get("HVSC_MIRROR", DEFAULT_MIRROR).rstrip("/")

# id -> HVSC relative path.  Both tunes are the JCH NewPlayer byte-exact
# validation references (load $1000, init->$1060, play->$10E8).
TUNES = {
    "flexible": "MUSICIANS/S/Scorpio/Flexible.sid",
    "simple": "MUSICIANS/J/JCH/Simple_Tune.sid",
}


def fetch(relpath: str, *, force: bool = False, retries: int = 4) -> Path:
    """Fetch ``relpath`` from the HVSC mirror into the cache; return its path.

    Thin wrapper over :func:`pysidtracker.testing.fetch_tune`: validates the
    PSID/RSID magic, retries transient network failures with exponential
    backoff, writes the cache atomically, and returns the cached path.
    """
    return fetch_tune(
        relpath, cache_dir=CACHE, mirror=MIRROR, retries=retries, force=force
    )


def corpus_relpaths() -> list[str]:
    """Every HVSC relpath the tune-dependent test modules reference.

    Gathered from the test modules themselves (single source of truth), so a
    new corpus tune is fetched/cached in CI without editing this script.
    """
    rels = set(TUNES.values())
    sys.path.insert(0, str(REPO))
    modules = {
        "tests.test_corpus": (
            "SUPPORTED",
            "MODEL_RECOVERED",
            "REJECTED",
            "REJECTED_V0X_VARIANT",
        ),
        "tests.test_v20player": ("V20_TUNES", "NON_V20_FAMILY"),
    }
    for name, attrs in modules.items():
        try:
            mod = importlib.import_module(name)
        except Exception:  # pylint: disable=broad-except  # best-effort gather
            continue
        for attr in attrs:
            value = getattr(mod, attr, None)
            if isinstance(value, dict):
                rels.update(value.values())
            elif isinstance(value, (list, tuple)):
                rels.update(value)
    return sorted(rels)


def fetch_id(tune_id: str, *, force: bool = False) -> Path:
    """Fetch the tune registered under ``tune_id``."""
    return fetch(TUNES[tune_id], force=force)


def main(argv=None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--id", help="only this tune id")
    parser.add_argument("--force", action="store_true", help="re-download")
    parser.add_argument("--list", action="store_true", help="print id -> path")
    parser.add_argument(
        "--corpus",
        action="store_true",
        help="fetch every tune the test corpus needs (best-effort)",
    )
    args = parser.parse_args(argv)

    if args.list:
        for tid, rel in TUNES.items():
            print(f"{tid}\t{rel}")
        return 0

    if args.corpus:
        rels = corpus_relpaths()
        got, missing = 0, []
        for rel in rels:
            try:
                fetch(rel, force=args.force)
                got += 1
            except Exception as exc:  # pylint: disable=broad-except
                missing.append(rel)
                print(f"WARN: {rel}: {exc}", file=sys.stderr)
        print(f"corpus: fetched/cached {got}/{len(rels)} tunes into {CACHE}")
        if missing:
            print(
                f"unreachable: {len(missing)} ({', '.join(missing)})", file=sys.stderr
            )
        return 0

    ids = [args.id] if args.id else list(TUNES)
    for tid in ids:
        path = fetch_id(tid, force=args.force)
        print(f"{tid}: {TUNES[tid]} -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
