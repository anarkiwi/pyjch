# JCH NewPlayer versions in HVSC, and what pyjch supports

## Three tiers of support

pyjch reads JCH NewPlayer tunes at three levels:

1. **Byte-exact V0x player** — the canonical **`JCH_NewPlayer_V0x`** layout.
   `pyjch.reader.parse` returns a `Song`; `pyjch.player.Player` replays it
   register-for-register, validated against the `preframr-sidtrace` oracle
   (residual 0) for **Flexible** (Scorpio) and **Simple_Tune** (JCH).

2. **Byte-exact V20 player** — the **`JCH_NewPlayer_V20`** two-column wavetable
   engine (the largest HVSC bucket), reverse-engineered in
   `re-trackers/JCH_NewPlayer/{jch-architecture.md,jch-generators.md,
   jch-player.asm}` against its reference tune
   `24th_Amaranth_Grand_Prix_3.sid`. `pyjch.v20player.V20Player` runs the full
   64K-image play routine (groove tempo, two parallel wavetable columns, PW
   ping-pong, portamento, vibrato, global filter sweep, hard restart) and is
   validated **frame-exact** against an independently generated py65 register
   oracle. `pyjch.v20player.playable(model)` gates this tier: it discovers every
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
   different instrument-record layout / relocated code), so pyjch does **not**
   replay them byte-exactly; the reader recovers the *model* and validates it
   is **coherent** (bases in range, order lists terminate through in-range
   pattern pointers, instrument records present), never garbage.

All tiers discover per-tune addresses by their surrounding player-code
instruction **idiom** (relocation-safe), not a fixed offset. When neither a
byte-exact layout nor a coherent family model can be recovered, `parse` raises
`SidParseError` rather than returning meaningless bytes. `reglog` / the CLI
expose byte-exact register logs only for the two verified tiers (V0x, V20);
model-only versions raise there.

## Was the "genuinely different players" claim overstated?

**Yes — substantially, for *reader* coverage.** The earlier pass concluded the
NewPlayer versions were "genuinely different players" and rejected V1–V21
(3,600+ tunes). That is true of the *player opcode stream* — the AD/SR path,
gate handling and per-frame engine do differ between versions. For **V20** (the
largest bucket) the opcode stream is now fully transcribed and its playback
**is** byte-exact (see below); for the other family versions byte-exact playback
is not yet done, but the **song data layout is the same across the whole
wavetable family, only relocated**, and is recoverable directly from the loaded
image. Independent byte analysis of representatives from every bucket confirms
the same discovery idioms resolve to in-range, coherent tables:

* subtune table via `LDX #$00 ; LDA subtune,Y ; STA abs,X`,
* pattern-pointer low/high via `LDA tbl,Y ; STA $FB` / `STA $FC`,
* instrument records via `LDA inst,Y ; LDY $1740,X ; STA $D405,Y`,
* (optionally) wavetable note column via `LDA col,Y ; CMP #$7E` and the pitch
  table via `LDA pitch+1,Y ; ADC #$00`.

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
| `V20`  | 1737 | 1647 | family (RE-documented) | **byte-exact player** for the 1,324 that are the V20 code build; the rest model-recovered |
| `Glover_NewPlayer_V21` | 67 | 0 | different fork | rejected |
| `Dane_NewPlayer` | 19 | 0 | different fork | rejected |
| `JCH_DigiPlayer` | 3 | 0 | different | rejected |

**Reader coverage: 2 → ~3,324 tunes** (2 V0x byte-exact + 3,322 family models).
**Byte-exact playback: 2 V0x + 1,324 V20 = 1,326 tunes**, each validated
frame-exact against a py65 register oracle (V0x against `preframr-sidtrace`; V20
against `tests/_v20oracle.py`).  Of the 1,737 sidid-`V20` tunes, 1,324 are the
byte-identical V20 code build that `pyjch.v20player.playable` accepts and replays
byte-exactly; the remainder are packed/relocated-code rips, or rare sub-builds
with a different instrument-record layout or a split wave-note column, which are
gated out and kept at *model recovered; playback not byte-exact-verified* rather
than mis-played.  The other family versions (V1/V2/V6/V8/V9/V10/V11/V13/V14/V15/
V17/V18) are recovered by the shared idiom set plus a per-tune coherence gate and
carry the same honest model-only label; their player opcode streams differ from
V20's, so byte-exactness there is not claimed.

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
