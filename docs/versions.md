# JCH NewPlayer versions in HVSC, and what pyjch supports

## Three tiers of support

pyjch reads JCH NewPlayer tunes at three levels:

1. **Byte-exact V0x player** — the canonical **`JCH_NewPlayer_V0x`** layout.
   `pyjch.reader.parse` returns a `Song`; `pyjch.player.JchPlayer` replays it
   register-for-register, validated frame-for-frame against the `sidtrace` oracle for **Flexible** (Scorpio) and **Simple_Tune** (JCH).

2. **Byte-exact V20 player** — the **`JCH_NewPlayer_V20`** two-column wavetable
   engine (the largest HVSC bucket), reverse-engineered in
   `re-trackers/JCH_NewPlayer/{jch-architecture.md,jch-generators.md,
   jch-player.asm}` against its reference tune
   `24th_Amaranth_Grand_Prix_3.sid`. `pyjch.player.JchPlayer` runs the full
   64K-image play routine (groove tempo, two parallel wavetable columns, PW
   ping-pong, portamento, vibrato, global filter sweep, hard restart) and is
   validated **frame-exact** against the `sidtrace` register
   oracle. `pyjch.player.playable(model)` gates this tier: it discovers every
   V20 data table (subtune, wave note/ctrl columns, filter/PW programs,
   instruments, pattern pointers, command params, pitch table) plus the
   per-build hard-restart CTRL immediate and the fixed gate-off re-arm cells,
   confirms the instrument-record field offsets match the reference build, and
   returns byte bases only when the tune **is** the V20 code build. Data tables
   are packed at per-tune (variable) addresses, so every base is discovered by
   idiom; the player-code work cells are at fixed offsets from the code base
   (the tune's init address).

3. **Version-aware model reader** — the remaining JCH NewPlayer **wavetable
   family** versions (V1/V2/V6/V8/V9/V10/V11/V13/V14/V15/V17/V18 and the V20
   sub-builds the V20 player does not accept). `parse` returns a
   `pyjch.newplayer.NewPlayerModel`: the recovered **song DATA** — subtune
   table, per-voice order lists, pattern-pointer tables, instrument records,
   and (where present) the wavetable note column and pitch table. These
   versions run a *different player opcode stream* (or a V20 sub-build with a
   different instrument-record layout / relocated code); the reader recovers
   the *model* and validates it is **coherent** (bases in range, order lists
   terminate through in-range pattern pointers, instrument records present),
   never garbage. Playback is a separate concern and is byte-exact:
   `pyjch.player.JchPlayer` runs the tune's own 6502 driver on py65 via
   `pysidtracker.EmuPlayer`.

All tiers discover per-tune addresses by their surrounding player-code
instruction **idiom** (relocation-safe), not a fixed offset. When neither a
byte-exact layout nor a coherent family model can be recovered, `parse` raises
`SidParseError` rather than returning meaningless bytes. `reglog` / the CLI
expose byte-exact register logs for every recovered version (V0x/V20 from their
native engines, the rest via `EmuPlayer`).

## Was the "genuinely different players" claim overstated?

**Yes — substantially, for *reader* coverage.** The earlier pass concluded the
NewPlayer versions were "genuinely different players" and rejected V1–V21
(3,600+ tunes). That is true of the *player opcode stream* — the AD/SR path,
gate handling and per-frame engine do differ between versions. For **V20** (the
largest bucket) the opcode stream is now fully transcribed into a native engine;
the other family versions play byte-exactly by running their own 6502 driver on
py65 via `EmuPlayer`. Either way the **song data layout is the same across the
whole wavetable family, only relocated**, and is recoverable directly from the
loaded image. Independent byte analysis of representatives from every bucket confirms
the same discovery idioms resolve to in-range, coherent tables:

* subtune table via `LDX #$00 ; LDA subtune,Y ; STA abs,X`,
* pattern-pointer low/high via `LDA tbl,Y ; STA $FB` / `STA $FC`,
* instrument records via `LDA inst,Y ; LDY $1740,X ; STA $D405,Y`,
* (optionally) wavetable note column via `LDA col,Y ; CMP #$7E` and the pitch
  table via `LDA pitch+1,Y ; ADC #$00` (V17 instead reads the 16-bit table
  interleaved without the carry -- `TAY ; LDA base,Y ; STA ; LDA base+1,Y` --
  recovered by a distinct idiom gated on an ascending note table).

### Family wavetable-table lift (both wave columns + extra tables)

The V20 player discovers, beyond the required tables, the second **wave-ctrl
column**, the **pitch**, **command (cmdparam)**, **filter** and **PW-program**
tables. Those idioms embed V20-specific work-cell addresses, so they are
**generalized by role** (the per-build cells wildcarded) to lift the same tables
across the family:

* the **CTRL-shadow** cell is found by the per-frame blit
  `[LDA shadow,X] {LDY stride,X}? AND gate,X ; STA $D404,Y` (the extra `LDY`
  appears in V9-V11); the **wave-ctrl column** is then the table stored into it
  (`LDA ctrl,Y ; STA shadow,X`), anchored by `LDY wtptr,X` and confirmed by the
  wavetable-tick `INC wtptr,X` (rejecting V1/V2, whose CTRL comes from a
  per-instrument field group, not a pointer-indexed column);
* the parallel **wave-note column** is recovered from the `$7F`-jump handler
  `LDA ctrl,Y ; STA wtptr,X ; TAY ; LDA note,Y` or the note-column test read
  `LDY wtptr,X ; LDA note,Y ; {CMP #$7E/$7F | BMI/BPL}`;
* the **command table** via `ASL ; TAY ; LDA param,Y ; PHA`; the **filter** and
  **PW** programs via `LDY grvidx ; LDA filt,Y ; STA grvctr` and
  `LDY pwcur,X ; LDA pwnext,Y ; STA pwcur,X` (these last two share V20's opcode
  frame, so they generalize to the V20 sub-builds only).

The **classic family (V6-V18) filter/PW programs are *not* recovered** into the
editor format, and this is a genuine *engine* difference, not a column reorder or
a missing idiom. Disassembly of the V9 (`DRAX/Acid.sid`, PW engine `$1245`,
filter engine `$12e3`) and V13 (`Abaddon/Apina.sid`, PW cursor `$13ba`, filter
cursor `$1464`) sweep code shows the classic program is a **limit-based
ping-pong**: each 4-byte entry is `{packed up/down limits (hi/lo nibble), step,
flags+dwell, value}`, the cursor advances **sequentially** (`TYA ; CLC ; ADC #$04
; STA cursor`, **no next-index column**), and the accumulator **auto-reflects**
when it reaches the packed hi/lo limits (`ADC step ; CMP up-limit ; ... ; SBC
step ; CMP down-limit ; EOR direction`). The NP20-25 editor pulse/filter format
is a *next-index-chained* `{value, step, dwell, next-absolute-index}` model with
**no limits column and no reflection**. The classic **limits column is
load-bearing** (it drives the reflection) and has no editor representation, and
the editor's `next`/`value` columns occupy roles the classic format encodes
differently; converting would mean *simulating the accumulator* to re-encode a
hardware reflection as an entry chain -- a re-encoding, not a faithful structural
transform, and unverifiable with no classic-editor tune as ground truth. So these
programs are left absent (noted in `Provenance`) rather than fabricated. Only
V20-frame builds recover filter/PW. (The earlier "different column order, no next
column" note understated this: the real blocker is the reflection model.)

The **V1/V2 wave `$FF`-restart target is now *derived*** -- but full editor export
stays blocked at the instrument + PW/filter layers. Disassembly of V1
(`JCH/Beatbassie.sid`, walk at `$1488`) and V2 (`JCH/Caverns.sid`, walk at
`$14ba`) shows a **single interleaved `(ctrl,note)` stream** (V1 base `$170f`,
cursor `$1709,X`; V2 base `$1781`, cursor `$17f6,X`): even byte = waveform written
to the CTRL shadow, odd byte = note, cursor stepped `+2`/tick. A `$FF` ctrl byte
is a **restart** whose loop target is reloaded from a per-voice runtime cell (V1
`$170c,X`, V2 `$1826,X`). Tracing the seed: at instrument init the cell is written
**exactly once** from the instrument's wave-start field (V1 `$138d-$1394`: `LDA
$1778,Y ; ASL ; STA $1709,X ; STA $170c,X`), so the restart target **is the
instrument's own wave-start** -- the stream therefore *does* de-interleave to the
editor's two columns with a synthesised inline `$7F` target = wave-start (the
prior "off-table, not invertible" blocker for the wave layer is resolved; the
model records the interleaved stream base in `wave_stream` and notes the
derivation). A **faithful editor export is still blocked**, though, by two other
genuine differences: (i) V1/V2 instrument records are **16-byte** (`inst# * 16`,
`ASL A` ×4 at V1 `$125a`) with editor-incompatible field roles (AD `+0`, SR `+1`,
filter `+3`, PW-limits `+5/+6`, restart-mode `+7`, wave-start `+8`) -- not the
editor's 8-byte layout; and (ii) their PW/filter are the **same limit-based
ping-pong as the classic family, embedded per-instrument** (no shared program
table for the editor's `pwprog`/`filterprog` pointers to reference). With no
V1/V2 editor-form tune as ground truth to validate an instrument transpose, a
faithful export cannot be proven, so V1/V2 stay gated out of editor export.

With both wave columns and the pitch table recovered, `pyjch.extract` produces a
full family `Tune` and the editor `.prg` writer's `_require_tables` gate passes.
Over the JCH-dense HVSC dirs (`DRAX`/`Laxity`/`JCH`) **~954 of ~968** recovered
family/V20 models now recover both wave columns and **~895** pass the table gate
(up from zero for the non-V20-build family). A full editor export additionally
requires the song to fit the editor format capacity (≤114 patterns, ≤32
instruments, ≤256-byte tables). A sequence longer than the editor's 96-**row**
cap (a row is a player fetch unit -- zero or more command bytes then one note
byte, not a raw byte) is split losslessly in the writer into consecutive ≤96-row
chunk-sequences, with the voice's order list rewritten to reference them in order
(state persists across a sequence boundary: end-of-pattern only resets the
pattern cursor and advances the order pointer, so the split is behaviourally
identical). Tunes that still exceed a hard limit (a non-terminating sequence, or
>114 patterns after the split) are gated fully but not editor-exportable.

Per-version representative outcome (`tests/test_corpus.py::FAMILY_COVERAGE`):

| version | wave-ctrl | wave-note | pitch | command | gate | full export |
| ------- | :-------: | :-------: | :---: | :-----: | :--: | :---------: |
| V6/V8/V9/V10/V11/V13/V18 | Y | Y | Y | V18 only | Y | Y |
| V14/V15 | Y | Y | Y | – | Y | Y (96-row sequence split) |
| V17 | Y | Y | Y | – | Y | Y (interleaved pitch idiom, no `ADC #$00`) |
| V1/V2 | – | – | Y | – | – | interleaved (ctrl,note) wave stream; `$FF`-restart target *derived* (= instrument wave-start), so the wave layer de-interleaves -- but export blocked by 16-byte instrument records + embedded limit-based PW/filter |
| V20 (code build) | Y | Y | Y | Y | Y | Y |

## HVSC census and per-version verdict

Counts from a full `sidid -m` scan of the HVSC `C64Music` tree. "recovered" is
this reader's result over the whole bucket (parse → coherent `NewPlayerModel`,
or `Song` for V0x).

| sidid tag | tunes | recovered | data layout vs V0x | verdict |
| --------- | ----: | --------: | ------------------ | ------- |
| `V0x`  | 4    | 2 Song | canonical | byte-exact player (2); 2 are the table-gate variant below |
| `V1`   | 7    | 7   | family (relocated) | model recovered |
| `V2`   | 6    | 6   | family | model recovered |
| `V3`   | 1    | 0   | different | rejected |
| `V4`   | 6    | 0   | different | rejected (older pointer scheme) |
| `V5`   | 41   | 26  | mixed | partial: coherent tunes recovered, others rejected |
| `V6`   | 29   | 27  | family | model recovered |
| `V7`   | 1    | 0   | different | rejected |
| `V8`   | 17   | 16  | family | model recovered |
| `V9`   | 125  | 120 | family | model recovered |
| `V10`  | 79   | 76  | family | model recovered |
| `V11`  | 15   | 14  | family | model recovered |
| `V12`  | 33   | 30  | mixed | partial recovered |
| `V13`  | 56   | 55  | family | model recovered |
| `V14`  | 719  | 686 | family | model recovered (2nd-largest bucket) |
| `V15`  | 314  | 305 | family | model recovered |
| `V17`  | 238  | 206 | family | model recovered |
| `V18`  | 108  | 101 | family | model recovered |
| `V19`  | 56   | 0   | different pointer scheme | rejected (self-modified per-voice pointers, not a `,Y` table) |
| `V20`  | 1737 | 1647 | family (RE-documented) | **native byte-exact player** for the 1,324 that are the V20 code build; the rest of the recovered tunes play byte-exact via `EmuPlayer` |
| `Glover_NewPlayer_V21` | 67 | 0 | different fork | rejected |
| `Dane_NewPlayer` | 19 | 0 | different fork | rejected |
| `JCH_DigiPlayer` | 3 | 0 | different | rejected |

**Reader coverage: 2 → ~3,324 tunes** (2 V0x byte-exact + 3,322 family models).
**Byte-exact playback: every recovered tune** — V0x and the 1,324 V20 code-build
tunes through native pure-Python engines, the rest via `EmuPlayer` (the tune's
own 6502 driver on py65).  Of the 1,737 sidid-`V20` tunes, 1,324 are the
byte-identical V20 code build that `pyjch.player.playable` accepts for the
**native** V20 engine; the remainder are packed/relocated-code rips, or rare
sub-builds with a different instrument-record layout or a split wave-note column,
which are gated out of the native engine and play byte-exact via `EmuPlayer`
instead.  Both native routines of the unified `pyjch.player.JchPlayer` are
validated frame-exact against the shared `sidtrace` oracle
(`tests/test_oracle_hvsc.py`), historically also over a **random ~300-tune
sample** of the native-accepted set spanning many authors (100% frame-exact for
the full sampled horizon, 120–600 frames).  Per-tune verification of every one of
the 1,324 is not individually asserted — the guarantee is that `playable()`
admits only the byte-identical V20 build to the native engine.  The other family
versions (V1/V2/V6/V8/V9/V10/V11/V13/V14/V15/V17/V18) are recovered by the shared
idiom set plus a per-tune coherence gate; their player opcode streams differ from
V20's, so rather than a native transcription they play byte-exact via `EmuPlayer`
(also validated against the `sidtrace` oracle).

### V20 byte-exact validation

The V20 player is a faithful transcription of the reverse-engineered play
routine (`re-trackers/JCH_NewPlayer/`), driving a 64K memory image exactly as
the 6502 does.  It was checked **frame-exact** (all 25 registers `$D400..$D418`,
every frame) against independently generated py65 grids over a random sample of
~300 V20-build tunes across many authors — 100% of the accepted tunes matched
for the full sampled horizon (120–600 frames).  `playable()` is the soundness
gate: it discovers every data table by idiom, confirms the instrument-record
field offsets and the wave-note column indexing match the reference build, and
returns bases only for genuine V20 builds, so no tune is silently mis-played.

### Residual rejections (≈270 tunes, honest)

Within the recoverable buckets, a minority reject cleanly — almost all are
**packed / relocated tunes** whose `JMP init ; JMP play` vectors do not sit at
the standard offset (e.g. a game rip whose init stub relocates the player to
`$C000`), or a handful of minor sub-variants whose pattern-pointer idiom
differs. Recovering those would require running `init` to relocate the image
(the `RELOCATED`/`PACKED` detect path) or per-sub-variant idioms; they are
rejected rather than mis-parsed.

### Genuinely different (evidence + effort)

* **V19** (56): the pattern pointer is a self-modified per-voice pointer
  (`LDA abs,X ; STA zp`), not a `LDA table,Y` pointer table, so the family
  pattern-pointer idiom is absent. Effort: a V19-specific pointer reader
  (moderate — needs a V19 disassembly to confirm order/pattern semantics).
* **V3/V4/V7** (8): older pre-wavetable pointer schemes.
* **Glover_V21 / Dane / DigiPlayer** (89): separate forks with their own data
  layout.

## The V0x table-driven variant (Problems, Imagination)

`sidid` also tags `MUSICIANS/J/JCH/Problems.sid` and `.../Imagination.sid` as
V0x; they share the V0x **init** signature (so `recognize()` matches) but drive
the gate-on waveform from an instrument table, which neither the V0x player nor
the family reader models — so `parse` rejects them rather than emit garbage.

## Reproducing the census

```bash
export HVSC=/path/to/C64Music
SIDIDCFG=/path/to/sidid.cfg sidid -m "$HVSC"   # multiscan -> per-version tags
```

`tests/test_corpus.py` drives one deterministic representative per version
against a real HVSC tree (V0x parses to a byte-exact `Song`; each family
version parses to a coherent `NewPlayerModel`; every unrecoverable version is
cleanly rejected), plus a bulk walk of JCH-dense directories asserting that
*every* tune the family reader accepts yields a coherent model.
```
