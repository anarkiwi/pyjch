"""Command line interface: song info and register logs."""

import argparse
import sys

from pyjch import reglog
from pyjch.errors import JCHError, SidParseError
from pyjch.model import Song
from pyjch.newplayer import NewPlayerModel
from pyjch.reader import read


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
    if isinstance(song, NewPlayerModel):
        raise SidParseError(
            f"{song.version}: song model recovered, but byte-exact playback is "
            "not supported for this JCH NewPlayer family version (V0x only)"
        )
    frames = round(args.seconds * 50)
    writes = reglog.iter_register_writes(song, max_frames=frames)
    reglog.write_reglog(writes, args.output)
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
