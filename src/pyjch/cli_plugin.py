"""Register JCH NewPlayer as a ``pysidtracker`` CLI format.

Installing pyjch adds a ``jch`` format to the one generic ``pysidtracker``
command (``pysidtracker.formats`` entry-point group), so
``pysidtracker info/reglog/wav tune.sid`` works for JCH tunes -- plus a
JCH-specific ``export`` subcommand -- with no separate ``pyjch`` binary.
"""

from __future__ import annotations

from pathlib import Path
from typing import List

from pysidtracker import FormatCommand, SidFormat

from pyjch.editor import np_profile, write_editor_prg
from pyjch.extract import extract
from pyjch.newplayer import NewPlayerModel
from pyjch.player import JchPlayer, playable
from pyjch.reader import JchSidParser, read
from pyjch.serialize import to_json, to_text


def _describe(model) -> List[str]:
    """The JCH-specific ``info`` lines for a recovered ``model``."""
    lines: List[str] = []
    if isinstance(model, NewPlayerModel):
        if playable(model) is not None:
            lines.append("player:      JCH NewPlayer V20 (byte-exact, native)")
        else:
            lines.append(
                f"player:      JCH NewPlayer family ({model.version}); "
                "byte-exact via EmuPlayer"
            )
        lines.append(f"subtune tbl: ${model.subtune_table:04X}")
        lines.append(
            f"pattern ptr: ${model.patternptr_lo:04X} / ${model.patternptr_hi:04X}"
        )
        lines.append(f"instruments: ${model.instruments:04X}")
    else:
        lines.append("player:      JCH_NewPlayer_V0x (byte-exact, native)")
        lines.append(f"AD/SR:       ${model.init_ad:02X} / ${model.init_sr:02X}")
        lines.append(f"gate on/off: ${model.gate_ctrl:02X} / ${model.gateoff_ctrl:02X}")
        lines.append(f"subptr lo/hi:${model.subptr_lo:04X} / ${model.subptr_hi:04X}")
    for voice in range(3):
        lines.append(
            f"  voice {voice}: orderlist @ ${model.orderlist_ptr(0, voice):04X}"
        )
    return lines


def _add_export_arguments(subparser) -> None:
    subparser.add_argument("song", help="JCH NewPlayer .sid/.prg file")
    subparser.add_argument("output", help="output file (.json/.txt/.prg)")
    subparser.add_argument(
        "--format", choices=("json", "text", "editor-prg"), default="json"
    )
    subparser.add_argument(
        "--np-version",
        type=int,
        choices=(20, 21, 22, 23, 24, 25),
        default=25,
        help="editor-prg NP version (default 25)",
    )
    subparser.add_argument("--driver", help="stock player .prg for --format editor-prg")


def _export(args, fmt) -> None:  # pylint: disable=unused-argument
    tune = extract(read(args.song))
    if args.format == "text":
        Path(args.output).write_text(to_text(tune), encoding="utf-8")
    elif args.format == "editor-prg":
        driver = Path(args.driver).read_bytes() if args.driver else b""
        prg = write_editor_prg(tune, driver=driver, profile=np_profile(args.np_version))
        Path(args.output).write_bytes(prg)
    else:
        Path(args.output).write_text(to_json(tune), encoding="utf-8")
    print(f"wrote {args.output}")


FORMAT = SidFormat(
    name="jch",
    parser=JchSidParser(),
    player=JchPlayer,
    describe=_describe,
    commands=(
        FormatCommand(
            "export",
            "export the recovered JCH song model (json/text/editor-prg)",
            _add_export_arguments,
            _export,
        ),
    ),
)
