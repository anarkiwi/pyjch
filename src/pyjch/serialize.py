"""Serialize a :class:`~pyjch.songmodel.Tune` to JSON / text and back.

:func:`to_json` / :func:`from_json` are an exact round trip (``str``-valued
enums serialize by value and reconstruct by value); :func:`to_text` is a compact
human dump.  The JSON form is the stable interchange surface for the exporter.
"""

import json
from dataclasses import asdict
from enum import Enum
from typing import List, Optional

from pyjch.songmodel import (
    Command,
    CommandEvent,
    CommandKind,
    FilterStep,
    Instrument,
    NoteEvent,
    NoteKind,
    OrderEntry,
    OrderList,
    PatternEvent,
    Provenance,
    PwStep,
    Subtune,
    Tune,
    WaveTable,
)


def _enum_default(obj):
    if isinstance(obj, Enum):
        return obj.value
    raise TypeError(f"not JSON serializable: {type(obj).__name__}")


def to_json(tune: Tune) -> str:
    """Serialize ``tune`` to an indented JSON string."""
    return json.dumps(asdict(tune), indent=2, default=_enum_default)


def _note(data) -> Optional[NoteEvent]:
    if data is None:
        return None
    return NoteEvent(NoteKind(data["kind"]), data["value"])


def _command(data) -> Optional[CommandEvent]:
    if data is None:
        return None
    kind = data["kind"]
    return CommandEvent(
        CommandKind(kind) if kind is not None else None,
        data["byte"],
        arg=data["arg"],
        delay=data["delay"],
        cmd_index=data["cmd_index"],
        param_lo=data["param_lo"],
        param_hi=data["param_hi"],
    )


def _pattern(events) -> List[PatternEvent]:
    return [
        PatternEvent(e["raw"], _note(e["note"]), _command(e["command"])) for e in events
    ]


def _order(data) -> OrderList:
    entries = [OrderEntry(e["pattern"], e["transpose"]) for e in data["entries"]]
    return OrderList(entries, data["loops"], data["restart"], data["raw"])


def _subtune(data) -> Subtune:
    return Subtune([_order(o) for o in data["order_lists"]], data["tempo"])


def _instrument(data) -> Instrument:
    return Instrument(**data)


def _wavetable(data) -> Optional[WaveTable]:
    if data is None:
        return None
    return WaveTable(data["note_col"], data["ctrl_col"])


def _command_row(data) -> Command:
    kind = data["kind"]
    return Command(data["lo"], data["hi"], CommandKind(kind) if kind else None)


def from_json(text: str) -> Tune:
    """Reconstruct a :class:`~pyjch.songmodel.Tune` from :func:`to_json` output."""
    data = json.loads(text)
    prov = data["provenance"]
    return Tune(
        name=data["name"],
        author=data["author"],
        released=data["released"],
        load_addr=data["load_addr"],
        init_addr=data["init_addr"],
        play_addr=data["play_addr"],
        hard_restart=data["hard_restart"],
        subtunes=[_subtune(s) for s in data["subtunes"]],
        patterns=[_pattern(p) for p in data["patterns"]],
        pattern_raw=[list(p) for p in data["pattern_raw"]],
        instruments=[_instrument(i) for i in data["instruments"]],
        wavetable=_wavetable(data["wavetable"]),
        pw_program=[PwStep(**s) for s in data["pw_program"]],
        filter_program=[FilterStep(**s) for s in data["filter_program"]],
        groove=data["groove"],
        pitch_table=data["pitch_table"],
        commands=[_command_row(c) for c in data["commands"]],
        provenance=Provenance(
            prov["tier"], prov["version"], prov["bases"], prov["notes"]
        ),
    )


def _event_text(event: PatternEvent) -> str:
    if event.note is not None:
        return f"{event.note.kind.value}:{event.note.value:02X}"
    cmd = event.command
    return f"{cmd.kind.value if cmd.kind else '?'}({event.raw:02X})"


def to_text(tune: Tune) -> str:
    """Compact, stable human dump of ``tune``."""
    lines = [
        f"name: {tune.name}",
        f"author: {tune.author}",
        f"released: {tune.released}",
        f"load/init/play: ${tune.load_addr:04X}/${tune.init_addr:04X}/"
        f"${tune.play_addr:04X}",
        f"tier: {tune.provenance.tier} ({tune.provenance.version})",
        f"subtunes: {len(tune.subtunes)}  patterns: {len(tune.patterns)}  "
        f"instruments: {len(tune.instruments)}  commands: {len(tune.commands)}",
    ]
    for si, sub in enumerate(tune.subtunes):
        lines.append(f"subtune {si} tempo={sub.tempo:02X}")
        for vi, order in enumerate(sub.order_lists):
            steps = " ".join(
                f"{e.pattern:02X}" + (f"+{e.transpose:02X}" if e.transpose else "")
                for e in order.entries
            )
            end = "loop" if order.loops else "stop"
            lines.append(f"  voice {vi}: [{steps}] {end}")
    for pi, events in enumerate(tune.patterns):
        lines.append(f"pattern {pi}: " + " ".join(_event_text(e) for e in events))
    for ii, inst in enumerate(tune.instruments):
        lines.append(
            f"inst {ii}: AD={inst.ad:02X} SR={inst.sr:02X} "
            f"wave={inst.wave_speed} abs={int(inst.abs_freq)} "
            f"filt={inst.filter_mode:02X}/{inst.filter_route:X} "
            f"pw={inst.pw_prog:02X} wstart={inst.wave_start:02X}"
        )
    if tune.provenance.notes:
        lines.append("notes:")
        lines.extend(f"  - {note}" for note in tune.provenance.notes)
    return "\n".join(lines) + "\n"
