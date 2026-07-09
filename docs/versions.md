# JCH NewPlayer versions in HVSC, and what pyjch supports

## Verdict

pyjch models exactly one player: the **canonical `JCH_NewPlayer_V0x`** layout.
Its reader (`pyjch.reader.parse`) and player (`pyjch.player.Player`) are a
byte-exact transcription of that one 6502 routine, validated against the
`preframr-sidtrace` register oracle (residual 0) for both HVSC tunes that use
it directly: **Flexible** (Scorpio) and **Simple_Tune** (JCH).

`parse` discovers the per-tune values by their surrounding instruction bytes
(idiom search), not by a fixed offset, so it survives relocation and the small
per-tune code shifts a fixed offset cannot. When those idioms are absent it
**raises `SidParseError`** rather than reading meaningless bytes out of a
different player's code.

## HVSC census (via `sidid`, full C64Music tree)

`sidid` distinguishes the NewPlayer binaries with distinct byte signatures.
Bucketed counts, and whether pyjch's reader accepts them:

| sidid version tag        | HVSC tunes | pyjch parses | note |
| ------------------------ | ---------: | :----------: | ---- |
| `JCH_NewPlayer_V0x`      | 4          | 2            | canonical; 2 accepted, 2 are a table-driven variant (below) |
| `JCH_NewPlayer_V1`       | 7          | no           | different player |
| `JCH_NewPlayer_V2`       | 6          | no           | different player |
| `JCH_NewPlayer_V3`       | 1          | no           | different player |
| `JCH_NewPlayer_V4`       | 6          | no           | different player |
| `JCH_NewPlayer_V5`       | 41         | no           | different player |
| `JCH_NewPlayer_V6`       | 29         | no           | different player |
| `JCH_NewPlayer_V7`       | 1          | no           | different player |
| `JCH_NewPlayer_V8`       | 17         | no           | different player |
| `JCH_NewPlayer_V9`       | 125        | no           | different player |
| `JCH_NewPlayer_V10`      | 79         | no           | different player |
| `JCH_NewPlayer_V11`      | 15         | no           | different player |
| `JCH_NewPlayer_V12`      | 33         | no           | different player |
| `JCH_NewPlayer_V13`      | 56         | no           | different player |
| `JCH_NewPlayer_V14`      | 719        | no           | different player |
| `JCH_NewPlayer_V15`      | 311        | no           | different player |
| `JCH_NewPlayer_V17`      | 235        | no           | different player |
| `JCH_NewPlayer_V18`      | 106        | no           | different player |
| `JCH_NewPlayer_V19`      | 56         | no           | different player |
| `JCH_NewPlayer_V20`      | 1729       | no           | different player (most common) |
| `Glover_NewPlayer_V21`   | 67         | no           | different player |
| `Dane_NewPlayer`         | 19         | no           | different player |
| `JCH_DigiPlayer`         | 3          | no           | different player |
| (no version sub-tag)     | 102        | no*          | mixed bucket sidid did not sub-classify |
| **total**                | **3767**   | **2**        | |

\* one `<none>`-bucket tune carries the V0x init signature; it is not part of
the deterministic corpus sample.

## Why the other versions are not "just relocations"

They are genuinely different programs, not the same binary moved in memory:

- **AD/SR init.** The canonical V0x seeds attack/decay and sustain/release from
  immediates: `LDA #imm ; STA $D405` / `... $D406`. This idiom is present in
  **all 4** V0x tunes and in **essentially none** of V1..V21 — those versions
  drive AD/SR from an instrument/wavetable, so there is no per-tune immediate to
  read at all. Discovering AD/SR the V0x way is meaningless for them.
- **Distinct signatures.** `sidid` needs a separate byte signature per version
  precisely because each play routine differs.
- **Opcode-stream format.** pyjch's player interprets the V0x opcode stream
  ($80–$9F transpose, `<$80` subpattern push/pop, gate/tempo counters). The
  other versions use different stream encodings that this player cannot walk.

## The V0x table-driven variant (Problems, Imagination)

`sidid` also tags `MUSICIANS/J/JCH/Problems.sid` and `.../Imagination.sid` as
V0x, and they do share the V0x **init** signature (so `recognize()` truthfully
matches them). But their **play** routine loads the gate-on waveform CTRL from
an instrument table (`LDA $13xx,Y ; STA $D404,Y`) instead of a fixed immediate,
so they carry only one `LDA #imm ; STA $D404,Y` idiom, not two. pyjch's player
writes a single immediate gate-on CTRL and does not model the table, so `parse`
rejects them rather than play them incorrectly. Supporting this variant would
require modelling the instrument-table waveform path in the player (future
work), and a `preframr-sidtrace` oracle to prove it byte-exact.

## Reproducing the census

```bash
export HVSC=/path/to/C64Music
SIDIDCFG=/path/to/sidid.cfg sidid -m "$HVSC"   # multiscan -> per-version tags
```

The corpus test (`tests/test_corpus.py`) drives one deterministic representative
per version against a real HVSC tree, asserting the supported tunes parse and
recognise and every other version is cleanly rejected.
