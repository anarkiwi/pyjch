"""Where a spec first stops matching a recording of another, for one tune.

``spec_diff.py SPEC_TRACE.py REF.pkl.gz SPEC.play TUNE.sid [--frames N] [--count K]`` plays the tune
under SPEC and prints, for the first K calls that differ from the recording, each register or edge
that differs (``spec_trace.py`` is pysidtracker's ``tools/spec_trace.py``).
"""

import argparse
import gzip
import importlib.util
import pickle


def load(path):
    """The module at ``path``."""
    found = importlib.util.spec_from_file_location("spec_trace", path)
    module = importlib.util.module_from_spec(found)
    found.loader.exec_module(module)
    return module


def main() -> None:
    """Print the differing registers of the first calls that differ."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("tool")
    parser.add_argument("ref")
    parser.add_argument("spec")
    parser.add_argument("tune")
    parser.add_argument("--frames", type=int, default=3000)
    parser.add_argument("--count", type=int, default=5)
    args = parser.parse_args()
    tool = load(args.tool)
    with gzip.open(args.ref, "rb") as handle:
        want = pickle.load(handle)[args.tune]
    _, have = tool.trace((args.tune, args.spec, args.frames))
    if isinstance(have, str) or isinstance(want, str):
        print(have if isinstance(have, str) else want)
        return
    shown = 0
    for call, (a, b) in enumerate(zip(tool.audible(want), tool.audible(have))):
        if a == b:
            continue
        regs = [i for i, (p, q) in enumerate(zip(a[0], b[0])) if p != q]
        print(
            call,
            {i: (a[0][i], b[0][i]) for i in regs},
            "edges" if a[1] != b[1] else "",
            a[1] if a[1] != b[1] else "",
            b[1] if a[1] != b[1] else "",
        )
        shown += 1
        if shown >= args.count:
            break


if __name__ == "__main__":
    main()
