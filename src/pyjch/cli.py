"""Command line interface: song info and register logs."""

import argparse
from pathlib import Path

from pysidtracker import add_reglog_command, print_info, run_cli

from pyjch import player, reglog
from pyjch.editor import np_profile, write_editor_prg
from pyjch.errors import JCHError, SidParseError
from pyjch.extract import extract
from pyjch.model import Song
from pyjch.newplayer import NewPlayerModel
from pyjch.reader import read
from pyjch.serialize import to_json, to_text


def _info_common(song) -> None:
    print_info(
        song.name,
        song.author,
        song.released,
        song.load_addr,
        song.init_addr,
        song.play_addr,
    )


def _info_v0x(song: Song) -> None:
    print("player:      JCH_NewPlayer_V0x (byte-exact)")
    print(f"AD/SR:       ${song.init_ad:02X} / ${song.init_sr:02X}")
    print(f"gate on/off: ${song.gate_ctrl:02X} / ${song.gateoff_ctrl:02X}")
    print(f"subptr lo/hi:${song.subptr_lo:04X} / ${song.subptr_hi:04X}")
    for voice in range(3):
        addr = song.orderlist_ptr(0, voice)
        print(f"  voice {voice}: orderlist @ ${addr:04X}")


def _info_family(song: NewPlayerModel) -> None:
    if player.playable(song) is not None:
        print("player:      JCH NewPlayer V20 (byte-exact)")
    else:
        print(f"player:      JCH NewPlayer family ({song.version}); model recovered")
    print(f"subtune tbl: ${song.subtune_table:04X}")
    print(f"pattern ptr: ${song.patternptr_lo:04X} / ${song.patternptr_hi:04X}")
    print(f"instruments: ${song.instruments:04X}")
    for voice in range(3):
        addr = song.orderlist_ptr(0, voice)
        print(f"  voice {voice}: orderlist @ ${addr:04X}")


def _info(args) -> None:
    song = read(args.song)
    _info_common(song)
    if isinstance(song, NewPlayerModel):
        _info_family(song)
    else:
        _info_v0x(song)


def _reglog(args) -> None:
    song = read(args.song)
    if isinstance(song, NewPlayerModel) and player.playable(song) is None:
        raise SidParseError(
            f"{song.version}: song model recovered, but byte-exact playback is "
            "not supported for this JCH NewPlayer family version "
            "(byte-exact players: V0x, V20)"
        )
    frames = round(args.seconds * 50)
    writes = reglog.iter_register_writes(song, max_frames=frames)
    reglog.write_reglog(writes, args.output)
    print(f"wrote {args.output}")


def _export(args) -> None:
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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pyjch", description="JCH NewPlayer song tools"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    info = commands.add_parser("info", help="print song metadata")
    info.add_argument("song", help="JCH NewPlayer .sid/.prg file")
    info.set_defaults(func=_info)

    add_reglog_command(commands, _reglog, song_help="JCH NewPlayer .sid/.prg file")

    exp = commands.add_parser("export", help="export the recovered song model")
    exp.add_argument("song", help="JCH NewPlayer .sid/.prg file")
    exp.add_argument("output", help="output file (.json/.txt/.prg)")
    exp.add_argument("--format", choices=("json", "text", "editor-prg"), default="json")
    exp.add_argument(
        "--np-version",
        type=int,
        choices=(20, 21, 22, 23, 24, 25),
        default=25,
        help="editor-prg NP version (default 25)",
    )
    exp.add_argument("--driver", help="stock player .prg for --format editor-prg")
    exp.set_defaults(func=_export)
    return parser


def main(argv=None) -> int:
    """CLI entry point; returns a process exit code."""
    return run_cli(_parser, JCHError, argv)


if __name__ == "__main__":
    import sys

    sys.exit(main())
