"""Command line interface: song info and register logs."""

import argparse
import sys
from pathlib import Path

from pyjch import reglog, v20player
from pyjch.editor import np_profile, write_editor_prg
from pyjch.errors import JCHError, SidParseError
from pyjch.extract import extract
from pyjch.model import Song
from pyjch.newplayer import NewPlayerModel
from pyjch.reader import read
from pyjch.serialize import to_json, to_text


def _info_common(song) -> None:
    print(f"name:        {song.name}")
    print(f"author:      {song.author}")
    print(f"released:    {song.released}")
    print(f"load:        ${song.load_addr:04X}")
    print(f"init/play:   ${song.init_addr:04X} / ${song.play_addr:04X}")


def _info_v0x(song: Song) -> None:
    print("player:      JCH_NewPlayer_V0x (byte-exact)")
    print(f"AD/SR:       ${song.init_ad:02X} / ${song.init_sr:02X}")
    print(f"gate on/off: ${song.gate_ctrl:02X} / ${song.gateoff_ctrl:02X}")
    print(f"subptr lo/hi:${song.subptr_lo:04X} / ${song.subptr_hi:04X}")
    for voice in range(3):
        addr = song.orderlist_ptr(0, voice)
        print(f"  voice {voice}: orderlist @ ${addr:04X}")


def _info_family(song: NewPlayerModel) -> None:
    if v20player.playable(song) is not None:
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
    if isinstance(song, NewPlayerModel) and v20player.playable(song) is None:
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

    log = commands.add_parser("reglog", help="write a SID register log")
    log.add_argument("song", help="JCH NewPlayer .sid/.prg file")
    log.add_argument("output", help="register log file to write")
    log.add_argument("--seconds", type=float, default=60.0)
    log.set_defaults(func=_reglog)

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
    args = _parser().parse_args(argv)
    try:
        args.func(args)
    except (JCHError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
