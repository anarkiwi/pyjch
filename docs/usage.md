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

## Command line

```bash
pyjch info   tune.sid
pyjch reglog tune.sid tune.reglog --seconds 30
```

## Public API

- `read(src)` / `parse(bytes)` — read a `.sid`/`.prg` into a `Song` (V0x) or
  `NewPlayerModel` (wavetable family).
- `JchPlayer(model)` — one `pysidtracker.MemPlayer` that plays every recovered
  version byte-exactly: native pure-Python engines for V0x (from a `Song`) and
  V20 (from a playable `NewPlayerModel`), and every other recovered version via
  `pysidtracker.EmuPlayer` (the tune's own 6502 driver on py65);
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
