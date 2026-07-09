# JCH NewPlayer versions in HVSC, and what pyjch supports

## Two tiers of support

pyjch reads JCH NewPlayer tunes at two levels:

1. **Byte-exact player** — the canonical **`JCH_NewPlayer_V0x`** layout.
   `pyjch.reader.parse` returns a `Song`; `pyjch.player.Player` replays it
   register-for-register, validated against the `preframr-sidtrace` oracle
   (residual 0) for **Flexible** (Scorpio) and **Simple_Tune** (JCH).

2. **Version-aware model reader** — the later JCH NewPlayer **wavetable
   family** (the two-column wavetable engine reverse-engineered in
   `re-trackers/JCH_NewPlayer/`, whose disassembly is a `JCH_NewPlayer_V20`
   tune, the largest HVSC bucket). `parse` returns a
   `pyjch.newplayer.NewPlayerModel`: the recovered **song DATA** — subtune
   table, per-voice order lists, pattern-pointer tables, instrument records,
   and (where present) the wavetable note column and pitch table. These
   versions run a *different player opcode stream*, so pyjch does **not**
   replay them byte-exactly; the reader recovers the *model* and validates it
   is **coherent** (bases in range, order lists terminate through in-range
   pattern pointers, instrument records present), never garbage.

Both tiers discover per-tune addresses by their surrounding player-code
instruction **idiom** (relocation-safe), not a fixed offset. When neither the
V0x layout nor a coherent family model can be recovered, `parse` raises
`SidParseError` rather than returning meaningless bytes.

## Was the "genuinely different players" claim overstated?

**Yes — substantially, for *reader* coverage.** The earlier pass concluded the
NewPlayer versions were "genuinely different players" and rejected V1–V21
(3,600+ tunes). That is true of the *player opcode stream* — the AD/SR path,
gate handling and per-frame engine do differ, so byte-exact **playback** of
these versions is not yet done. But the **song data layout is the same across
the whole wavetable family, only relocated**, and is recoverable directly from
the loaded image. Independent byte analysis of representatives from every
bucket confirms the same discovery idioms resolve to in-range, coherent tables:

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
| `V20`  | 1737 | 1647 | family (RE-documented) | model recovered (largest bucket) |
| `Glover_NewPlayer_V21` | 67 | 0 | different fork | rejected |
| `Dane_NewPlayer` | 19 | 0 | different fork | rejected |
| `JCH_DigiPlayer` | 3 | 0 | different | rejected |

**Reader coverage: 2 → ~3,324 tunes** (2 V0x byte-exact + 3,322 family models).
The V20 layout is verified field-by-field against the full disassembly; the
other family versions are recovered by the shared idiom set plus a per-tune
coherence gate (order lists walk to a terminator through in-range pattern
pointers), and are labelled *model recovered; playback not byte-exact-verified*.

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
