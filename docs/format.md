# JCH NewPlayer format

For the full per-version HVSC census and support verdict, see
[versions.md](versions.md).

## Overview

JCH NewPlayer (JCH / Jens-Christian Huus) is a C64 tracker player used across
the demoscene. `pyjch` reads it at three levels:

- **Byte-exact V0x player** — the canonical `JCH_NewPlayer_V0x` layout
  (Flexible, Simple_Tune): `parse` returns a `Song` the player replays
  register-for-register.
- **Byte-exact V20 player** — the `JCH_NewPlayer_V20` two-column wavetable
  engine (largest HVSC bucket): `pyjch.player.JchPlayer` replays the 1,324
  tunes that are the V20 code build, each validated frame-exact against the
  `sidtrace` register oracle. `pyjch.player.playable(model)` is the soundness gate.
- **Model reader** — the remaining JCH NewPlayer *wavetable family*
  (V1/V2/V6/V8/V9/V10/V11/V13/V14/V15/V17/V18 and V20 sub-builds, ~2,000 HVSC
  tunes): `parse` returns a `NewPlayerModel` recovering the song DATA
  (subtune/order-list/pattern/instrument tables) directly from the image. These
  versions run a different player opcode stream, so playback is not
  byte-exact-verified.

A few genuinely different players (V3/V4/V7/V19, `Glover_NewPlayer_V21`,
`Dane_NewPlayer`, `JCH_DigiPlayer`) and packed/relocated tunes are cleanly
**rejected** rather than mis-parsed.

## Container and detection notes

Tunes are consumed as `.sid` (PSID/RSID) containers or bare `.prg` images
through the shared [`pysidtracker`](https://github.com/anarkiwi/pysidtracker)
base. The player binary is identical across tunes of a given version; only its
DATA and the addresses that data lives at differ, so the reader **discovers**
each per-tune immediate and table base by matching the surrounding instruction
bytes (an idiom search — relocation-safe and robust to per-tune code shifts a
fixed offset cannot survive). Container headers are not trusted; packed/relocated
tunes whose `JMP init ; JMP play` vectors do not sit at the standard offset are
rejected rather than mis-parsed. When neither a byte-exact V0x layout nor a
coherent family model is recoverable, `parse` raises `SidParseError`.

## Data model

Three independent per-voice opcode streams. The V0x reader discovers, by idiom:

- **AD/SR defaults** — the immediates in `LDA #imm ; STA $D405` / `... $D406`.
- **Gate-off / gate-on CTRL** — the two immediates in `LDA #imm ; STA $D404,Y`.
- **Subpattern pointer-table bases** — the operands in `LDA abs,Y ; STA $fb`
  (lo) / `... $fc` (hi).
- **Orderlist pointers** — three per-voice pairs at `$1010`, indexed by
  `subtune * 8`; the 7th byte seeds the tempo.
- **Frequency tables** — `$121F` (lo) / `$1220` (hi), 0x80 entries, indexed by
  note with a per-voice transpose.

When these V0x idioms are absent, `parse` falls back to the wavetable-family
model reader (`pyjch.newplayer`), which discovers the family table bases —
subtune table, pattern-pointer low/high, instrument records, and (where present)
wavetable note column / pitch table — by their own idioms and returns a
`NewPlayerModel` if the recovered song is coherent (order lists walk to a
terminator through in-range pattern pointers). `Song` resolves `orderlist_ptr` /
`subpattern_ptr`.

Init (`FUN_1060`) copies the orderlist pointer pairs into per-voice cur/base
pointers, loads the tempo, and seeds the SID registers. Play (`FUN_10E8`) walks
each voice's opcode stream via the zero-page pointer `$fb/$fc`: transpose-set
opcodes (`$80–$9F`), subpattern references (`<$80` in the orderlist push a
pointer and jump via the subpattern tables), gate/tempo counters (`$80–$8F`
inside a subpattern), and note bytes that index the frequency table.

## Player and playback notes

`JchPlayer(model)` exposes `.play_frame() -> list[(reg, val)]` and `.regs`;
`iter_frames` / `render_grid` and `pysidtracker.register_writes_from_player`
provide the shared `py*` register-log surface. The CLI `reglog` command emits
byte-exact register logs only for the two verified tiers (V0x, V20); model-only
versions raise there.

### Byte-exact verdict

Both driver versions are validated frame-for-frame against the shared
`pysidtracker` `sidtrace` register oracle — a patched `sidplayfp` run in Docker
— framed at the JCH player's fixed PAL cadence (19656 cycles/frame,
forward-filled, leading silent play-calls aligned over, PW-hi registers
nibble-masked). See `tests/test_oracle_hvsc.py`:

| Tune | Author | Version | Frames | Result |
| --- | --- | --- | --- | --- |
| Flexible | Scorpio | V0x | 250 | **byte-exact** |
| Simple Tune | JCH | V0x | 250 | **byte-exact** |
| 24th Amaranth Grand Prix 3 | DRAX | V20 | 250 | **byte-exact** |
| 7D Funkt | Impetigo | V20 | 250 | **byte-exact** |
| Stories | DRAX | V20 | 250 | **byte-exact** |

`playable()` admits only the byte-identical V20 build, so no tune is silently
mis-played.

## References

- [versions.md](versions.md) — full per-version HVSC census and support verdict.
- [editor-format.md](editor-format.md) — the JCH-Editor NP22-25 native song
  layout (export target: header pointer table, tables, encodings; sourced).
- [exporter-plan.md](exporter-plan.md) — staged plan to implement the structural
  exporter (neutral model → JSON / editor-native `.prg`) in this library.
- `sidtrace` byte-exact register oracle (`tests/test_oracle_hvsc.py`, `-m oracle`).
- [`pysidtracker`](https://github.com/anarkiwi/pysidtracker) — shared
  container/image/detection base.
