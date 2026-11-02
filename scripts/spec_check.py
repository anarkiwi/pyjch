"""Elaborate a packaged spec against a tune's binding and print its diagnostics.

``spec_check.py TUNE.sid [SUBTUNE]`` binds the spec ``make_player`` would run for the tune to the
memory its init leaves, so the static checks of ``pysidtracker playspec check`` see the real layout.
"""

import sys
from pathlib import Path

from pysidtracker.playspec import elaborate

from pyjch import parse
from pyjch.player import JchPlayer, spec_binding


def main() -> int:
    """Print the diagnostics, exit 1 when there are any."""
    model = parse(Path(sys.argv[1]).read_bytes())
    subtune = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    bound = spec_binding(model)
    program = elaborate(bound.spec, bound.bind(JchPlayer(model, subtune).view(), None))
    print(program.name, "elaborates")
    return 0


if __name__ == "__main__":
    sys.exit(main())
