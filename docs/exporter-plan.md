# Plan: JCH-Editor exporter for pyjch

Implementation plan for adding a **structural exporter** to pyjch: recover a
tune's high-level structures (playroutine-independent) and re-emit them (a) as a
neutral serialized model and (b) as a JCH-Editor-loadable `.prg`. The byte layout
is specified in [editor-format.md](editor-format.md); this is the code plan.

## Objective and staging

Deliver in fidelity tiers so each stage is testable before the next:

1. **Neutral model + JSON** — fully decode every recoverable table into a
   documented, player-independent model and serialize it. Testable offline today
   against the synthetic V20 fixture (`tests/_synth_v20.py`).
2. **Whole-family coverage** — extend static table discovery so *every* V1–V20
   wavetable-family build yields a coherent model (not just the V20 code build),
   with an honest per-table "recovered / absent" report.
3. **Editor-native `.prg` writer** — assemble the data region + `$0Fxx` pointer
   block at the canonical addresses; validate by round-tripping back through
   pyjch's own reader and (as an oracle) the SF2 `converter_jch.cpp` semantics.
4. **NP22-25 exactness** — confirm the `[P]` parameters (version magic, offsets,
   instrument field widths, pitch table) from the release `.d64` and wire them as
   a driver profile.

Non-goals (initially): byte-exact NP22-25 output before the `.d64` parameters are
confirmed; the genuinely different forks (V19, Glover/Dane/DigiPlayer).

## Architecture (new modules)

```
reader.parse ─► NewPlayerModel ─┐
                                ├─ extract.extract() ─► songmodel.Tune ─┬─ serialize (JSON/text)
player.playable    ─► V20Bases ─┘                                       └─ editor.write_editor_prg()
```

- **`pyjch/songmodel.py`** — neutral dataclasses (JSON/`asdict`-friendly):
  `Tune` (metadata + tables + `Provenance`), `Subtune`, `OrderList`/`OrderEntry`,
  pattern events (`PatternEvent`/`NoteEvent`/`CommandEvent` with `NoteKind`/
  `CommandKind` enums), `Instrument` (decoded 8-byte record), `WaveTable`
  (note+ctrl columns), `PwStep`, `FilterStep`, `Command`, plus `groove` and
  `pitch_table`. `Provenance` records which bases were found and the decode tier.
- **`pyjch/extract.py`** — `extract(model) -> Tune`. Statically walks the tables
  the reader located (reusing `newplayer` bases and, when present, `V20Bases`):
  order lists (`$FE`/`$FF`/`$80+`), patterns (`$7F` end; `$80`/`$A0`/`$C0`
  commands via the command table), instruments, wave/pw/filter programs, groove,
  pitch, commands. Never emits garbage: a table that will not decode coherently
  is left absent and noted in `Provenance`.
- **`pyjch/serialize.py`** — `to_json(tune)` / `from_json(...)` and a compact
  text dump; the stable interchange surface.
- **`pyjch/editor.py`** — `write_editor_prg(tune, *, driver: bytes, profile) ->
  bytes` per the write-side spec. `driver` is the stock NP player prefix (sourced
  at runtime, never committed); `profile` carries the `[P]` parameters. Ships
  scaffolded with the verified NP20 profile; raises a clear error until a profile
  is supplied for the requested version.
- **CLI** (`pyjch/cli.py`) — `pyjch export SONG OUT.json` and
  `pyjch export SONG OUT.prg --format editor-prg --driver DRIVER.prg`.

## Table-length strategy

Tables are not length-prefixed. Bound each by: (a) natural terminators where they
exist — patterns `$7F`, order lists `$FE`/`$FF`; (b) the next-higher discovered
base for the block tables (wave/pw/filter/command/pitch), clamped to image end;
(c) referenced-index maxima (instruments/commands referenced by patterns). Store
both the decoded structure and the raw bounded bytes so the model re-serializes
byte-identically.

## Coverage (100% structural, family)

- V20 code build (~1,324 HVSC tunes): full decode via `V20Bases` today.
- Remaining V1–V18 family builds: `newplayer.discover` already yields subtune /
  pattern-ptr / instrument (+ optional wave-note / pitch) for ~3,322 tunes →
  subtunes, order lists, patterns and instruments extract now. The wave-ctrl,
  pw, filter and command tables need per-family idioms — the "leverage the tiny
  playroutine" work: read each build's small play routine to locate those bases.
  Track coverage as a per-table matrix in the corpus test; the goal is every
  family tune yields a coherent `Tune`, byte-exact where a driver profile exists.

## Testing

- **Offline unit** — `tests/_synth_v20.py` already carries every idiom + coherent
  data; assert `extract` decodes the known subtunes/patterns/instruments/tables,
  JSON round-trips, and `write_editor_prg` (NP20 profile, dummy driver) produces
  the pointer block + tables at the canonical addresses; re-parse the emitted
  data region to confirm the round trip.
- **Corpus** (HVSC-gated, existing harness) — every family representative
  extracts to a coherent `Tune`; per-table coverage counts asserted.
- **Cross-check** — port `converter_jch.cpp`'s NP20 read logic as a test oracle:
  our emitted image must parse back to the same structures.

## Risks / gaps

- **Copyrighted driver blob** — the player prefix is sourced at runtime from the
  user's `.d64`, never committed; the exporter takes it as an argument.
- **NP22-25 `[P]` unknowns** — version magic, offsets, instrument widths, pitch
  bytes; blocked on the `.d64` (JC64dis). NP20 profile ships first.
- **Instrument field decode** — the native record layout is unpublished; model it
  from the RE (`re-trackers`) + GoatTracker and gate exact widths behind a
  profile.

## Directive compliance

Black + pylint clean (no unused imports/vars); pytest under xdist; coverage
> 85% (the synth fixture keeps the extractor/serializer covered offline);
numpy-first only where it helps (table decode is light); no script over 60 s; no
copyrighted material committed. Dependabot/CI already present. Land per module on
a branch, watch CI green.
