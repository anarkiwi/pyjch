# JCH NewPlayer format

For the full per-version HVSC census and support verdict, see
[versions.md](versions.md).

## Overview

JCH NewPlayer (JCH / Jens-Christian Huus) is a C64 tracker player used across
the demoscene. `pyjch` reads it at three levels:

- **Byte-exact V0x player** — the canonical `JCH_NewPlayer_V0x` layout
  (Flexible, Simple_Tune): `parse` returns a `Song` that `pyjch.player.JchPlayer`
  replays register-for-register from the playspec
  [`v0x.play`](../src/pyjch/specs/v0x.play).
- **Byte-exact V20 player** — the `JCH_NewPlayer_V20` two-column wavetable
  engine (largest HVSC bucket): `pyjch.player.JchPlayer` runs the playspec
  [`newplayer.play`](../src/pyjch/specs/newplayer.play) on the memory the tune's
  own init leaves. It replays the tunes that are the reference V20 code build,
  each validated frame-exact against the `sidtrace` register oracle and
  memory-exact against the tune's own 6502 run.
  `pyjch.player.playable(model)` is the soundness gate.
- **Model reader** — the remaining JCH NewPlayer *wavetable family*
  (V1/V2/V5/V6/V8/V9/V10/V11/V12/V13/V14/V15/V17/V18 and V20 sub-builds, ~2,000 HVSC
  tunes): `parse` returns a `NewPlayerModel` recovering the song DATA
  (subtune/order-list/pattern/instrument tables) directly from the image. These
  versions also play byte-exactly: `make_player` returns a
  `pysidtracker.EmuPlayer` running the tune's own 6502 driver on jennings (recovering the model and byte-exact
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
- **Frequency table** — one 16-bit little-endian entry per note from `$121F`
  (low byte) / `$1220` (high byte), 96 notes: the table then runs into the work
  cells at `$12DF`. The voice reads entry `2 * note + transpose`.

When these V0x idioms are absent, `parse` falls back to the wavetable-family
model reader (`pyjch.newplayer`), which discovers the family table bases —
subtune table, pattern-pointer low/high, instrument records, and (where present)
wavetable note column / pitch table — by their own idioms and returns a
`NewPlayerModel` if the recovered song is coherent (order lists walk to a
terminator through in-range pattern pointers). `Song` resolves `orderlist_ptr` /
`subpattern_ptr`.

Init (`FUN_1060`) copies the orderlist pointer pairs into per-voice cur/base
pointers, loads the tempo, and seeds the SID registers. Play (`FUN_10E8`) saves
`$FB/$FC`, walks each voice's opcode stream through them, and restores them. Each
voice advances at most one note per call, and only when its gate counter has run
out; the counters tick down once every tempo reload. The bytes are listed under
[Song stream grammar](#song-stream-grammar) (`v0x_orderlist`, `v0x_subpattern`).

## Song stream grammar

The streams the wavetable family's player reads are specified by
[`newplayer.lark`](../src/pyjch/newplayer.lark), a `pysidtracker` `ByteGrammar`
(one latin-1 character per byte). `extract` and `NewPlayerModel.iter_orderlist`
read them through it, so a stream that does not match is a `SidParseError`, not
a silently truncated song. `pysidtracker grammar TUNE` lists the parsed streams.

| Rule | Bytes | Meaning |
| ---- | ----- | ------- |
| `orderlist` | `$00`-`$7F` | pattern index |
| | `$80`-`$FD` | transpose for the next pattern index (the last one wins) |
| | `$FE` / `$FF` | stop / loop; ends the list |
| `pattern` | `$00`-`$7E` | note (`$00` rest, `$7E` hold) |
| | `$80`-`$FF` | command (`$8x` duration, `$Ax` instrument, `$C0`+ command-table index) |
| | `$7F` | ends the pattern |
| `pattern_v1` | `$00`-`$7F` / `$80`-`$FE` / `$FF` | V1/V2 (interleaved wave stream): note / command / end |
| `v0x_orderlist` | `$00`-`$7F` | run subpattern `n` (through the subpattern pointer tables) |
| | `$80`-`$9F` | set the voice transpose to `(byte & $1F) - $0C` semitones, until the next set |
| | `$A0`-`$FF` | end of list: restart at the list's first byte |
| `v0x_subpattern` | `$00` / `$7F` | rest (gate off) / tie (gate held) |
| | `$01`-`$7E` | note, an index into the frequency table with the transpose added |
| | `$80`-`$8F` | set the note duration; read in the same call as the byte after it |
| | `$90`-`$FF` | end of subpattern: back to the order list |
| `instrument` | 8 bytes | AD, SR, wave flags, filter, filter program, pulse program, wave start 1/2 |
| `step` | 4 bytes | pulse or filter program record |

Bytes stored after a terminator belong to the next stream; the player never
reads them through this stream's pointer.

## Player and playback notes

`make_player(model)` (a `JchPlayer` or an `EmuPlayer`) exposes `.play_frame() -> list[(reg, val)]` and `.regs`;
`iter_frames` / `render_grid` and `pysidtracker.register_writes_from_player`
provide the shared `py*` register-log surface. The CLI `reglog` command emits
byte-exact register logs for every recovered version (V0x and V20 from their
specs, the rest via `EmuPlayer`).

### Byte-exact verdict

Every recovered JCH NewPlayer version plays byte-exactly: V0x and V20 through
their playspecs, and every other recovered family version
(V1/V2/V5/V6/V8/V9/V10/V11/V12/V13/V14/V15/V17/V18) by running the tune's own 6502
driver on jennings via `pysidtracker.EmuPlayer`. All are validated frame-for-frame
against the shared `pysidtracker` `sidtrace` register oracle — a patched
`sidplayfp` run in Docker — over 60 seconds each, framed at the tune's own clock
(`cycles_per_frame_for_flags`: PAL 19656 or NTSC 17095 cycles/frame,
forward-filled, leading silent play-calls aligned over, PW-hi registers
nibble-masked). Every version in the table below is validated; a representative
subset:

| Tune | Author | Version | Engine | Result |
| --- | --- | --- | --- | --- |
| Flexible | Scorpio | V0x | playspec | **byte-exact** |
| 7D Funkt | Impetigo | V20 | playspec | **byte-exact** |
| Acid 1988 | JCH | V6 | EmuPlayer | **byte-exact** |
| Lunardive (NTSC) | Ahz | V15 | EmuPlayer | **byte-exact** |
| 1st Chaff | — | V17 | EmuPlayer | **byte-exact** |

`playable()` admits only the reference V20 build to the spec player: the work-cell
and table idioms must resolve, and the code the spec states but the SID registers
do not show (the saved zero-page pointer, the filter-program start) must match.
A build that differs there is gated out as `code-differs`; every other recovered
version plays byte-exactly through `EmuPlayer`, so no tune is silently mis-played.

## HVSC oracle survey

[`scripts/oracle_hvsc.py`](../scripts/oracle_hvsc.py) plugs `make_player` into the
[`pysidtracker.survey`](https://github.com/anarkiwi/pysidtracker/blob/main/docs/survey.md)
whole-song comparison against the `sidtrace` oracle (Docker), over every subtune
of every HVSC tune SIDId names `JCH_NewPlayer` or a `(JCH_NewPlayer_Vnn)`
sub-version (`PLAYER`). The `-m oracle` test renders through the same function.
Its `info` reports the decoded build: `engine` (`v0x`/`v20` playspec, or `emu`
for `EmuPlayer`), `version` (`V0x`, or the family play-prologue class from
`newplayer.classify_version`), and `pysidtracker.cadence.cadence_info`'s
`rsid`, play `trigger`, `speed` (calls per PAL frame) and `dynamic`. A tune neither reader recovers raises `JCHParseError` (a
`pysidtracker.SidParseError`), which the survey records as `not_decompiled`.

```bash
pip install "pysidtracker[survey]>=0.35.0"
python -m pysidtracker.survey find $HVSC --player '^\(?jch_newplayer(_v\w+)?\)?$' --out .hvsc-survey/tunes.csv
PYTHONPATH=scripts python -m pysidtracker.survey run $HVSC .hvsc-survey/tunes.csv \
    --work .hvsc-survey --render oracle_hvsc:render --jobs 24
python -m pysidtracker.survey report .hvsc-survey/results.csv
python -m pysidtracker.survey triage .hvsc-survey
PYTHONPATH=scripts python -m pysidtracker.survey diff $HVSC .hvsc-survey/tunes.csv <md5>_<song> \
    --work .hvsc-survey --render oracle_hvsc:render
```

`.hvsc-survey/` holds copyright-derived traces and is gitignored.

## References

- [versions.md](versions.md) — full per-version HVSC census and support verdict.
- [editor-format.md](editor-format.md) — the JCH-Editor NP22-25 native song
  layout (`pyjch.editor` export/re-read target: header pointer table, tables,
  encodings; sourced).
- `sidtrace` byte-exact register oracle (`tests/test_oracle_hvsc.py`, `-m oracle`;
  whole-HVSC survey above).
- [`pysidtracker`](https://github.com/anarkiwi/pysidtracker) — shared
  container/image/detection base.
