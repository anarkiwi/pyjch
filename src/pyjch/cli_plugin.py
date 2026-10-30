"""Register JCH NewPlayer as a ``pysidtracker`` CLI format.

Installing pyjch adds a ``jch`` format to the one generic ``pysidtracker``
command (``pysidtracker.formats`` entry-point group), so
``pysidtracker info/reglog/wav tune.sid`` works for JCH tunes -- plus a
JCH-specific ``export`` subcommand -- with no separate ``pyjch`` binary.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator, List

from pysidtracker import FormatCommand, SidFormat
from pysidtracker.bytegrammar import Stream

from pyjch import grammar
from pyjch.editor import np_profile, profile_for_tune, write_editor_prg
from pyjch.errors import SidParseError
from pyjch.extract import extract
from pyjch.newplayer import NewPlayerModel
from pyjch.player import make_player
from pyjch.reader import JchSidParser, read
from pyjch.serialize import to_json, to_text
from pyjch.variant import detect_variant, facet_sites


def _describe(model) -> List[str]:
    """The JCH-specific ``info`` lines for a recovered ``model``."""
    lines: List[str] = []
    if isinstance(model, NewPlayerModel):
        if detect_variant(model)["engine"] == "v20":
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


def _streams(model) -> Iterator[Stream]:
    """The order lists, patterns and instrument records the player reads."""
    if not isinstance(model, NewPlayerModel):
        return
    tune = extract(model)
    parse = grammar.GRAMMAR.parse_exact
    rule = grammar.pattern_rule(model)
    for index, sub in enumerate(tune.subtunes):
        for voice, order in enumerate(sub.order_lists):
            yield (
                f"order list {index}.{voice}",
                parse(bytes(order.raw), grammar.ORDERLIST),
                grammar.ORDERLIST,
            )
    for index, raw in enumerate(tune.pattern_raw):
        yield f"pattern {index}", parse(bytes(raw), rule), rule
    for index, inst in enumerate(tune.instruments):
        yield (
            f"instrument {index}",
            parse(bytes(inst.raw), grammar.INSTRUMENT),
            grammar.INSTRUMENT,
        )


def _tune_json(model) -> bytes:
    """The recovered song model as JSON, the derived artifact a snapshot digests."""
    return to_json(extract(model)).encode()


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
        default=None,
        help="editor-prg NP version (default: the input tune's own version)",
    )
    subparser.add_argument(
        "--driver",
        help="work-file .prg (load $0F00) supplying the player code and $0FA0 "
        "block; required for --format editor-prg",
    )


def _read_driver(path, profile) -> bytes:
    """Read a driver ``.prg``: strip and check its 2-byte load address."""
    if not path:
        raise SidParseError("--format editor-prg needs --driver (a work-file .prg)")
    raw = Path(path).read_bytes()
    if len(raw) < 2:
        raise SidParseError(f"driver {path} too short")
    load = raw[0] | (raw[1] << 8)
    if load != profile.load_addr:
        raise SidParseError(
            f"driver load address ${load:04X} != ${profile.load_addr:04X}"
        )
    return raw[2:]


def _export(args, fmt) -> None:  # pylint: disable=unused-argument
    tune = extract(read(args.song))
    if args.format == "text":
        Path(args.output).write_text(to_text(tune), encoding="utf-8")
    elif args.format == "editor-prg":
        profile = (
            profile_for_tune(tune)
            if args.np_version is None
            else np_profile(args.np_version)
        )
        prg = write_editor_prg(
            tune, driver=_read_driver(args.driver, profile), profile=profile
        )
        Path(args.output).write_bytes(prg)
    else:
        Path(args.output).write_text(to_json(tune), encoding="utf-8")
    print(f"wrote {args.output}")


FORMAT = SidFormat(
    name="jch",
    parser=JchSidParser(),
    player=make_player,
    describe=_describe,
    variant=detect_variant,
    facet_sites=facet_sites,
    probes={"tune": _tune_json},
    grammar=grammar.GRAMMAR,
    streams=_streams,
    commands=(
        FormatCommand(
            "export",
            "export the recovered JCH song model (json/text/editor-prg)",
            _add_export_arguments,
            _export,
        ),
    ),
)
