#!/usr/bin/env python3
"""HVSC census: which tunes pyjch decompiles, and whether its player matches.

Walks a ``C64Music`` tree, decompiles every ``.sid`` with
:func:`pyjch.decompile_sid` (the library's own detection decides the
population) and, with ``--frames``, renders each decompiled tune with
:class:`pyjch.JchPlayer` and compares it frame by frame, per SID register,
(pulse-width high registers as their SID-read nibble) against the original
``.sid`` run on an emulator: jennings
(:func:`pysidtracker.register_grid`) for tunes with a play address, the
``sidtrace`` sidplayfp oracle for tunes that install their own interrupt.
Writes one JSON line per tune and prints the per-version summary.
"""

import argparse
import json
import os
import sys
import tempfile
from collections import Counter, defaultdict
from multiprocessing import Pool
from pathlib import Path

from tqdm import tqdm

for _var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
    os.environ.setdefault(_var, "1")

# pylint: disable=wrong-import-position
from pysidtracker import (  # noqa: E402
    PW_HI_REGS,
    aligned_match,
    cycles_per_frame_for_flags,
    read_sidtrace,
    register_grid,
    run_sidtrace,
    sidtrace_grid,
)

import pyjch  # noqa: E402
import pyjch.song  # noqa: E402


def _oracle(path: Path, data: bytes, header, frames: int):
    if header.play_address:
        return register_grid(data, frames), 0
    with tempfile.TemporaryDirectory(dir=os.environ.get("CENSUS_TMP")) as tmp:
        csv = run_sidtrace(path, Path(tmp) / "trace.csv.zst", seconds=frames // 50 + 2)
        cpf = cycles_per_frame_for_flags(header.flags)
        return sidtrace_grid(read_sidtrace(csv), cycles_per_frame=cpf)[:frames], 4


def _nibbles(grid):
    """The pulse-width high registers as the SID reads them: their low nibble."""
    return [
        [v & 0x0F if r in PW_HI_REGS else v for r, v in enumerate(row)] for row in grid
    ]


def census_one(args):
    """One tune's record: decode outcome, build facts and player equivalence."""
    path, root, frames = args
    rec = {"path": str(path.relative_to(root))}
    data = path.read_bytes()
    try:
        dec = pyjch.decompile_sid(data)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        rec["error"] = f"{type(exc).__name__}: {exc}"[:200]
        return rec
    info = dec.info
    try:
        song, song_data = pyjch.song.decode(dec.layout)
        rec["roundtrip"] = pyjch.song.encode(song) == song_data
    except Exception as exc:  # pylint: disable=broad-exception-caught
        rec["roundtrip_error"] = f"{type(exc).__name__}: {exc}"[:200]
    rec.update(
        version=info.version,
        kind=info.kind.value,
        host=info.host,
        voices=info.voices,
        native=info.native,
    )
    if frames:
        try:
            oracle, lead = _oracle(path, data, dec.header, frames)
            oracle = _nibbles(oracle)
            got = _nibbles(pyjch.JchPlayer(dec.layout).render_grid(len(oracle) + lead))
            if lead:
                rec["equal"] = aligned_match(oracle, got, max_lead=lead)
            else:
                bad = [i for i, (a, b) in enumerate(zip(oracle, got)) if a != b]
                rec["equal"], rec["first_diff"] = not bad, bad[0] if bad else None
            rec["frames"] = len(oracle)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            rec["play_error"] = f"{type(exc).__name__}: {exc}"[:200]
    return rec


def summarize(records):
    """Per-version counts: decoded, native, player-equivalent."""
    rows = defaultdict(Counter)
    for rec in records:
        if "version" not in rec:
            continue
        key = (rec["version"], rec["voices"])
        row = rows[key]
        row["decoded"] += 1
        row["native"] += rec["native"]
        row[rec["host"]] += 1
        row[rec["kind"]] += 1
        if "equal" in rec:
            row["compared"] += 1
            row["equal"] += rec["equal"]
        row["play_error"] += "play_error" in rec
        row["roundtrip"] += rec.get("roundtrip", False)
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n", maxsplit=1)[0])
    ap.add_argument("hvsc", type=Path)
    ap.add_argument("--frames", type=int, default=0)
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--out", type=Path, default=Path("census.jsonl"))
    ap.add_argument(
        "--only", type=Path, help="a file of tune paths relative to the tree"
    )
    a = ap.parse_args(argv)
    if a.only:
        paths = [a.hvsc / line for line in a.only.read_text().split()]
    else:
        paths = sorted(a.hvsc.rglob("*.sid"))
    work = [(p, a.hvsc, a.frames) for p in paths]
    with Pool(a.jobs) as pool, a.out.open("w") as out:
        records = []
        for rec in tqdm(
            pool.imap_unordered(census_one, work, chunksize=4), total=len(work)
        ):
            records.append(rec)
            out.write(json.dumps(rec) + "\n")
    rows = summarize(records)
    for (version, voices), row in sorted(rows.items()):
        print(
            f"{version:16} {voices}v  "
            + "  ".join(f"{k} {v}" for k, v in sorted(row.items()))
        )
    total = sum(r["decoded"] for r in rows.values())
    print(f"decoded {total} of {len(records)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
