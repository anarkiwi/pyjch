# Usage

Reading a `.sid`/`.prg` into a `Song` and iterating per-frame writes is covered
in the [README](../README.md). This document covers the rest of the API. For the
format and players, see [format.md](format.md).

```python
import pyjch

song = pyjch.read("tune.sid")            # PSID/RSID/.sid or bare .prg

# Per-frame SID register writes (changed registers only, after frame 0).
for writes in pyjch.iter_frames(song, max_frames=50 * 60):
    ...                                  # writes: list[(register, value)]

# Forward-filled 25-register-per-frame snapshot grid (the oracle form).
grid = pyjch.render_grid(song, nframes=400)

# Register log (clock reg val triples).
pyjch.write_reglog(
    pyjch.register_writes_from_player(pyjch.make_player(song), max_frames=2500),
    "tune.reglog",
)
```

## Command line

pyjch ships no binary of its own; installing it registers a `jch` format with
the shared `pysidtracker` command (via the `pysidtracker.formats` entry point).

```bash
pysidtracker info   tune.sid
pysidtracker reglog tune.sid tune.reglog --seconds 30
pysidtracker wav    tune.sid tune.wav
pysidtracker export tune.sid tune.prg --format editor-prg --driver work.prg   # JCH-specific
```

## Variant

`pyjch.detect_variant(model)` returns the build's `pysidtracker.Variant`: one
value per facet, each with its source (`detected@$addr`, `default` or
`inferred(...)`). The facets are `model`, `version` (play-prologue class),
`engine` (`v0x`/`v20`/`emu`), `v20_gate` (`reference`, or the sub-build gate that
rejected the V20 spec player), `hard_restart`, `wave_layout`, `pitch_form`,
`ctrl_shadow_form`, `entry_points` and `v0x_layout`. It is the `variant` hook of
the registered `jch` format, so the survey `info` and the whydiff/pathdiff site
labels come from it, and `Tune.provenance.variant` records the same facets.

## Public API

- `read(src)` / `parse(bytes)` — read a `.sid`/`.prg` into a `Song` (V0x) or
  `NewPlayerModel` (wavetable family).
- `make_player(model, subtune=0)` — the byte-exact `pysidtracker.MemPlayer` of
  any recovered version: a `JchPlayer` for V0x (from a `Song`, the `v0x.play` playspec) and V20
  (from a playable `NewPlayerModel`, the `newplayer.play` playspec), both run on
  `pysidtracker.playspec.SpecPlayer`, else a `pysidtracker.EmuPlayer` running the
  tune's own 6502 driver on jennings; `.play_frame() -> list[(reg, val)]`,
  `.regs`, `.render_grid(nframes)`. `engine_of(player)` names the playroutine
  (`v0x` / `v20` / `emu`).
- `JchPlayer(model)` — the V0x / V20 spec player (`.engine`); any other version,
  or a V20 build that is not `playable`, raises `SidParseError`.
- `iter_frames(model, max_frames)` — per-frame writes.
- `render_grid(model, nframes) -> list[list[int]]` — forward-filled grid.
- `register_writes_from_player` / `read_reglog` / `write_reglog` / `RegWrite`
  (the shared `pysidtracker` register-log surface).
- `playable(model)` — the V20 spec-player gate (returns `V20Bases`, or `None`
  when `make_player` instead plays the tune byte-exact on `EmuPlayer`).
- Model: `Song` (with `orderlist_ptr` / `subpattern_ptr` resolution),
  `NewPlayerModel` (the wavetable-family reader result).
- Errors: `JCHError`, `SidParseError`.
