"""List the tunes whose spec build pyjch plays, per spec, for ``tools/spec_trace.py``.

``spec_tunes.py HVSC TUNES.csv OUTDIR`` reads a survey ``find`` CSV and writes
``OUTDIR/<spec>.csv`` (column ``path``, absolute) for each of ``v0x`` and ``newplayer``,
``emu.csv`` for the tunes played on ``EmuPlayer`` and ``refused.txt`` (``relpath<TAB>reason``)
for the V20 builds the spec refuses.
"""

import argparse
import csv
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from tqdm import tqdm

from pyjch import parse
from pyjch.constants import SPEC_REFUSALS
from pyjch.errors import SidParseError
from pyjch.model import Song
from pyjch.player import _engine, spec_gate


def build(path):
    """``(path, name)``: the spec that plays the tune at ``path``, a refusal reason, or ``emu``."""
    try:
        model = parse(Path(path).read_bytes())
        if not isinstance(model, Song):
            reason = spec_gate(model)[1]
            if reason in SPEC_REFUSALS:
                return path, reason
        return path, _engine(model)[1]
    except SidParseError:
        return path, "emu"


def main() -> None:
    """Write the per-spec tune lists and the refused ones."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("hvsc")
    parser.add_argument("tunes")
    parser.add_argument("outdir")
    parser.add_argument("--jobs", type=int, default=12)
    args = parser.parse_args()
    with open(args.tunes, encoding="utf-8") as handle:
        paths = [str(Path(args.hvsc) / row["path"]) for row in csv.DictReader(handle)]
    with ProcessPoolExecutor(args.jobs) as pool:
        found = list(tqdm(pool.map(build, paths, chunksize=16), total=len(paths)))
    out = Path(args.outdir)
    out.mkdir(parents=True, exist_ok=True)
    for name in sorted({name for _, name in found} - set(SPEC_REFUSALS)):
        with open(out / f"{name}.csv", "w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["path"])
            writer.writerows([path] for path, got in found if got == name)
        print(name, sum(got == name for _, got in found))
    refused = sorted((path, why) for path, why in found if why in SPEC_REFUSALS)
    with open(out / "refused.txt", "w", encoding="utf-8") as handle:
        for path, why in refused:
            handle.write(f"{Path(path).relative_to(args.hvsc)}\t{why}\n")
    print("refused", len(refused))


if __name__ == "__main__":
    main()
