# pyjch

A standalone, pure-Python **reader and player** for **JCH NewPlayer** C64
SID songs (the JCH / Jens-Christian Huus tracker player used across the
demoscene). It parses a JCH tune into a typed song model and runs the
playroutine to produce byte-exact per-frame SID register output, plus a
register-log surface for downstream tooling.

Everything (read/play/register-log) is **pure stdlib** — no dependencies.

```bash
pip install pyjch
```

## Quick start

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
    pyjch.iter_register_writes(song, max_frames=2500), "tune.reglog"
)
```

CLI:

```bash
pyjch info   tune.sid
pyjch reglog tune.sid tune.reglog --seconds 30
```

## Public API

- `read(src) -> Song` / `parse(bytes) -> Song` — read a `.sid`/`.prg`.
- `Player(song)` — `.play_frame() -> list[(reg, val)]`, `.regs`.
- `iter_frames(song, max_frames)` — per-frame writes.
- `render_grid(song, nframes) -> list[list[int]]` — forward-filled grid.
- `iter_register_writes` / `read_reglog` / `write_reglog` / `RegWrite`.
- Model: `Song` (with `orderlist_ptr` / `subpattern_ptr` resolution).
- Errors: `JCHError`, `SidParseError`.

## The JCH NewPlayer format

Three independent per-voice opcode streams. The player binary is identical
across tunes; only its DATA and the addresses that data lives at differ, so
the reader **discovers** each per-tune immediate and table base from the
player code's instruction operands (relocation-safe — no fixed-address
assumption):

- **AD/SR defaults** — `image[load+0xAA]` / `image[load+0xB5]`.
- **Gate-on / gate-off (rest) CTRL** — `image[load+0x1E1]` /
  `image[load+0x199]`.
- **Subpattern pointer-table bases** — the 16-bit operands at
  `load+0x12C` (lo) / `load+0x131` (hi).
- **Orderlist pointers** — three per-voice pairs at `$1010`, indexed by
  `subtune * 8`; the 7th byte seeds the tempo.
- **Frequency tables** — `$121F` (lo) / `$1220` (hi), 0x80 entries, indexed
  by note with a per-voice transpose.

Init (`FUN_1060`) copies the orderlist pointer pairs into per-voice
cur/base pointers, loads the tempo, and seeds the SID registers. Play
(`FUN_10E8`) walks each voice's opcode stream via the zero-page pointer
`$fb/$fc`: transpose-set opcodes (`$80–$9F`), subpattern references
(`<$80` in the orderlist push a pointer and jump via the subpattern
tables), gate/tempo counters (`$80–$8F` inside a subpattern), and note
bytes that index the frequency table.

## Byte-exact verdict

Validated against the `preframr-sidtrace` register oracle (PAL, 19656
cycles/frame, forward-filled, one leading silent play-call aligned over,
PW-hi registers nibble-masked):

| Tune | Author | Frames | Residual |
| --- | --- | --- | --- |
| Flexible | Scorpio | 368 | **0** (byte-exact) |
| Simple Tune | JCH | 368 | **0** (byte-exact) |

## Tests

Test tunes are HVSC copyright works and are **never committed**. They are
resolved from a local HVSC tree (`$JCH_LOCAL_HVSC`) or fetched on demand
(`python scripts/fetch_tunes.py`) into a gitignored cache; the byte-exact
tests validate against the live `preframr-sidtrace` binary when
`$SIDTRACE_BIN` is set, else against the committed frozen oracle grids in
`tests/fixtures/`, so CI passes with neither the binary nor the tunes.

```bash
pip install -e ".[dev]"
./run_tests.sh
SIDTRACE_BIN=/path/to/sidtrace ./run_tests.sh   # validate live, too
```

## License

Apache 2.0 — see `LICENSE`.
