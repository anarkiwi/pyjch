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
    pyjch.register_writes_from_player(pyjch.JchPlayer(song), max_frames=2500),
    "tune.reglog",
)
```

## Decompiling a `.sid`

```python
dec = pyjch.decompile_sid("tune.sid", subtune=0)
dec.song        # JchSong: the typed song (docs/song.md)
dec.info        # JchInfo: how the tune is built
dec.header      # the PSID/RSID header
dec.layout      # the located model (Song for V0x, else NewPlayerModel)
pyjch.JchPlayer(dec.layout, subtune=0).render_grid(500)
```

The NewPlayer core is found by its own `JMP init ; JMP play` pair, the one
whose init reads the subtune record; the container entries need not be it.

| `info` field | meaning |
| --- | --- |
| `version` | the core's play-prologue class (`V0x`, `V9-class`, `V13/V14-class`, `V11/V17-class`, `V15/V18-class`, `V20`, else `JCH_NewPlayer`) |
| `kind` | `PlayroutineKind`: `DIRECT` as loaded; `RELOCATED`/`PACKED` when the core exists only after the tune's init ran on jennings |
| `host` | `psid` (the header entries are the core's vectors), `wrapper` (a host routine calls the core), `irq` (the tune installs its own interrupt) |
| `code_base` | the core's vector pair; an image with several cores is read through the one the init enters for `subtune` |
| `voices` | 3, or 4 for the four-track sample build |
| `native` | replayed by a pure-Python engine; otherwise `JchPlayer` runs the tune's own code |
| `bases` | `V20Bases` for a V20 code build: every table, the gate-off re-arm cells, the hard-restart byte and the stored tuning's length; the work cells are `code_base` plus the `pyjch.player` offsets |
| `wrapper` | the banked-I/O host's cells and immediates (`pyjch.host.wrapper`) |
| `v0x` | `V0xBuild`: the V0x player's seeded attack/decay, sustain/release and gate bytes |

## Command line

pyjch ships no binary of its own; installing it registers a `jch` format with
the shared `pysidtracker` command (via the `pysidtracker.formats` entry point).

```bash
pysidtracker info   tune.sid
pysidtracker reglog tune.sid tune.reglog --seconds 30
pysidtracker wav    tune.sid tune.wav
pysidtracker export tune.sid tune.prg --format editor-prg   # JCH-specific
```

## Public API

- `decompile_sid(src, subtune=0)` — `DecompiledSid(song, info, header, layout, subtune)`.
- `decode(layout) -> (JchSong, SongData)` / `encode(song) -> SongData` — the typed
  song and its bytes ([song.md](song.md)).
- `read(src)` / `parse(bytes)` — read a `.sid`/`.prg` into a `Song` (V0x) or
  `NewPlayerModel` (wavetable family), statically.
- `JchPlayer(model)` — one `pysidtracker.MemPlayer` that plays every recovered
  version byte-exactly: native pure-Python engines for V0x (from a `Song`) and
  V20 (from a playable `NewPlayerModel`), and every other recovered version via
  `pysidtracker.EmuPlayer` (the tune's own 6502 driver on jennings);
  `.play_frame() -> list[(reg, val)]`, `.regs`, `.render_grid(nframes)`.
- `iter_frames(model, max_frames)` — per-frame writes.
- `render_grid(model, nframes) -> list[list[int]]` — forward-filled grid.
- `register_writes_from_player` / `read_reglog` / `write_reglog` / `RegWrite`
  (the shared `pysidtracker` register-log surface).
- `playable(model)` — the native-V20-engine gate (returns `V20Bases`, or `None`
  when the tune is instead played byte-exact via `EmuPlayer`).
- Model: `Song` (with `orderlist_ptr` / `subpattern_ptr` resolution),
  `NewPlayerModel` (the wavetable-family reader result).
- Errors: `JCHError`, `SidParseError`.
