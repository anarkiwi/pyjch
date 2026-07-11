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
  versions also play byte-exactly: `JchPlayer` runs the tune's own 6502 driver
  on py65 via `pysidtracker.EmuPlayer` (recovering the model and byte-exact
  playback are separate concerns).

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
byte-exact register logs for every recovered version (V0x/V20 from their native
engines, the rest via `EmuPlayer`).

### Byte-exact verdict

Every recovered JCH NewPlayer version plays byte-exactly: V0x and V20 through
their native pure-Python engines, and every other recovered family version
(V1/V2/V6/V8/V9/V10/V11/V13/V14/V15/V17/V18) by running the tune's own 6502
driver on py65 via `pysidtracker.EmuPlayer`. All are validated frame-for-frame
against the shared `pysidtracker` `sidtrace` register oracle — a patched
`sidplayfp` run in Docker — over 60 seconds each, framed at the tune's own clock
(`cycles_per_frame_for_flags`: PAL 19656 or NTSC 17095 cycles/frame,
forward-filled, leading silent play-calls aligned over, PW-hi registers
nibble-masked). Every version in the table below is validated; a representative
subset:

| Tune | Author | Version | Engine | Result |
| --- | --- | --- | --- | --- |
| Flexible | Scorpio | V0x | native | **byte-exact** |
| 7D Funkt | Impetigo | V20 | native | **byte-exact** |
| Acid 1988 | JCH | V6 | EmuPlayer | **byte-exact** |
| Lunardive (NTSC) | Ahz | V15 | EmuPlayer | **byte-exact** |
| 1st Chaff | — | V17 | EmuPlayer | **byte-exact** |

`playable()` admits only the byte-identical V20 build to the native engine;
every other recovered version plays byte-exactly through `EmuPlayer`, so no
tune is silently mis-played.

## References

- [versions.md](versions.md) — full per-version HVSC census and support verdict.
- [editor-format.md](editor-format.md) — the JCH-Editor NP22-25 native song
  layout (`pyjch.editor` export/re-read target: header pointer table, tables,
  encodings; sourced).
- `sidtrace` byte-exact register oracle (`tests/test_oracle_hvsc.py`, `-m oracle`).
- [`pysidtracker`](https://github.com/anarkiwi/pysidtracker) — shared
  container/image/detection base.
