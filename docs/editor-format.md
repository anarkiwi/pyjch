# JCH-Editor native song format (NP22-25)

Reference for the on-disk / in-memory song layout the **JCH-Editor 3.1 +
NP22-25** (Dane / Booze Design, 2011; [csdb #100406][rel]) loads, so a future
exporter can re-emit a pyjch-recovered song in a form the editor reads. This
documents the *editor's* native format; the per-tune *player* data layout pyjch
already recovers is in [format.md](format.md) / [versions.md](versions.md).

## Confidence

Every byte-exact, primary-source layout available publicly documents **JCH
NewPlayer 20.G / 20.G4** — the immediate predecessor of NP22-25. **No public
source gives the NP22-25 byte layout**; its authoritative spec lives only in two
binary files inside the release (`NP22-25 docs.doc`, `JCH 3.1+NP22-25.d64`),
which must be read/disassembled locally. Treat the tables below as
**NP20-verified, NP22-25-plausible-but-unconfirmed** until checked against the
`.d64`. Rows are tagged: **[V]** verified from source this doc cites, **[G]** gap
to resolve from the `.d64` / `docs.doc`.

## Container and entry points [V]

- The editor works on a **raw in-memory song**; a saved song is a **raw `.prg`
  memory dump at a fixed load address**, not a packed container.
- Load address **`$0F00`**; entry points **init `$1000`**, **play `$1003`**
  (a JCH NewPlayer `.prg` converts straight to `.sid` with these vectors).
- A separate **JCH-Packer** relocates/crunches a tune for final release
  (optional, occasionally lossy); "Syndrom's JCH-depacker" reverses it back to
  editor format. Export targets the *unpacked* editor `.prg`.

## Header pointer table `$0Fxx` [V, NP20]

The editor keeps every table's base in a fixed pointer block near the load
address (16-bit little-endian). This indirection is the heart of the native
format — the packed player inlines these operands, but the editor reads through
the table. Offsets verified from the SID Factory II JCH converter
([`converter_jch.cpp`][sf2]):

| Addr | Points at |
| ---- | --------- |
| `$0FA6` | init-data base; **default tempo byte = word[$0FA6] + 6** |
| `$0FBA` | fine-tune (speed/pitch) table |
| `$0FBC` | wave table |
| `$0FC0` | filter table |
| `$0FC2` | pulse table |
| `$0FC4` | instrument table |
| `$0FC6` | order list, voice 1 |
| `$0FC8` | order list, voice 2 |
| `$0FCA` | order list, voice 3 |
| `$0FCC` | sequence-vector low-byte table |
| `$0FCE` | sequence-vector high-byte table |
| `$0FD0` | command table |
| `$0FEE` | 4 ASCII version bytes, NP20 = `'2' '0' '.' 'G'` |

Sequence address: `seq_addr[i] = (hi_table[i] << 8) | lo_table[i]`.
Tempo special case: if the default tempo byte `< 2` (multispeed / funktempo),
real values come from `filter_table+1` / `filter_table+0`.

> NP22-25: **verify the version magic string and every offset** against the real
> player in the `.d64` — the converter's gate accepts only `"20.G"`, so NP22-25
> may use a different string and/or shifted offsets. **[G]**

## Table memory map (JCH 20.G4 cross-check) [V]

Independent map from [Codebase64][cb64] (self-declared "not 100% complete").
Tables at 256-byte (`$100`) intervals; order lists at `$400` intervals. This is
the same table set the `$0Fxx` pointers above resolve to in the reference build:

| Structure | Addr |
| --------- | ---- |
| Wave column 1 (note / arpeggio) | `$18CB` |
| Wave column 2 (waveform / ctrl) | `$19CB` |
| Filter table | `$1ACB` |
| Pulse table | `$1BCB` |
| Instrument table | `$1CCB` |
| Sequence pointers, low | `$1DCB` |
| Sequence pointers, high | `$1ECB` |
| Command / "Super" table | `$1FCB` |
| Order list, voice 0 / 1 / 2 | `$20CB` / `$24CB` / `$28CB` |
| Sequence data | from `$2CCB` |

## Order-list (sequence) encoding [V]

The **editor** order list is a stream of **fixed 2-byte pairs
`(transpose, seq_index)`** — verified verbatim from [`converter_jch.cpp`][sf2]'s
read loop:

```
transpose      = byte[read + off]
sequence_index = byte[read + off + 1]
entry.transpose = 0x20 + transpose     // editor zero-transpose baseline $20
if transpose == 0xFF: break            // end of list
```

This is **not** the packed-runtime order stream pyjch recovers (a variable
stream: `<$80` pattern index, `$80+` inline transpose prefix, `$FE` stop / `$FF`
loop). The editor form pairs every step with an explicit transpose byte; the
JCH-Packer collapses that into the compact runtime stream. **Exporting to the
editor therefore re-encodes** each recovered `OrderEntry(pattern, transpose)` as
`(0x20 + transpose, pattern)` and appends an `$FF`-transpose terminator.

Do **not** apply CheeseCutter's `$A0`-centred signed-transpose convention here —
that is a different, JCH-lineage driver. **[G]** the exact packed→editor
transpose mapping and loop-vs-stop restart semantics beyond `$FF` are still
unconfirmed for NP22-25.

## Sequence (pattern) event encoding [V]

Each event is a byte pair `(byte0, note)`. The decisive rule, verbatim from
[`converter_jch.cpp`][sf2]:

```
byte0 = byte[read + i]; note = byte[read + i + 1]
if byte0 == 0x7F: break        // end of sequence
if byte0 >= 0xC0: command = byte0            // a command (Super-table ref)
else:             instrument = byte0         // an instrument slot
```

So the converter's model is binary: `byte0 >= $C0` → command, else instrument
slot; `byte0 == $7F` ends. [Codebase64][cb64] refines the instrument-slot range
(`$80` no-op, `$90` tie, `$A0`–`$BF` select instrument `$00`–`$1F`). Note byte:
`$00` = gate off / rest, `$01+` = note, `$7E` = gate hold (`+++`). Examples:
`$A2 $24` = instrument 2 + C-3; `$80 $7E` = hold; `$90 $25` = tie to C#4.

## Command / Super table [V]

Verbatim from [`converter_jch.cpp`][sf2]: two columns, **row-major** on disk;
`col2 = col1 + row_count`; opcode = `byte & $F0`; **`$E0` = tempo** (value in the
paired column). SF2 transposes to column-major on import. The Super table lets
one `$C0`–`$DF` sequence byte stack several effects on a channel; the full
NP22-25 opcode list is only in `NP22-25 docs.doc`. **[G]**

## Instrument / wave / pulse / filter tables

`converter_jch.cpp` does **not** hard-code these layouts: it `CopyTable`s
wave/pulse/filter byte-for-byte and `CopyTableRowToColumnMajor`s the instrument
table, taking every **row/column count from the SF2 driver metadata**
(`DriverInfo::TableDefinition` for `sf2driver_np20`), not from the JCH file. So
this source fixes the table *geometry* (bytes-per-record = the NP20 driver's
column count) but not the per-byte *meaning*.

The per-byte **field meaning is already recovered in this repo's own RE** of an
NP20-family tune (`re-trackers/JCH_NewPlayer/jch-architecture.md` §3), and is
what `pyjch/extract.py` decodes: an **8-byte** instrument record
`AD, SR, wavspd/flags, filt, fprog, pwstart, wstart, wstart2`. The two remaining
unknowns for a byte-exact *editor* record are (a) any editor-only trailing bytes
(e.g. a 16-char name field) beyond the 8 the player consumes, and (b) NP22-25 vs
NP20 deltas — both resolved only from the `.d64`. GoatTracker v2 (JCH-derived)
is a cross-check for the field semantics, not a literal layout:

GoatTracker structural model ([readme §6.2][gt2]) — instrument fields:

| off | field |
| --- | ----- |
| +0 | attack/decay |
| +1 | sustain/release |
| +2 | wavetable pointer (`$00` = stop) |
| +3 | pulsetable pointer (`$00` = untouched) |
| +4 | filtertable pointer (`$00` = untouched) |
| +5 | vibrato param (speed-table index) |
| +6 | vibrato delay (`$00` disables) |
| +7 | gateoff / hard-restart timer; bit `$80` disables HR, `$40` disables gateoff |
| +8 | 1st-frame waveform (usually `$09`); `$00` leave, `$FE` gate off, `$FF` gate on |

Table block on disk (GoatTracker form): `+0` = row count `n`, then `n`
left-column bytes, then `n` right-column bytes.

- **Wavetable** (2 cols): left = wave/ctrl (`$00` no change; `$01`–`$0F` delay;
  `$10`–`$DF` waveform; `$E0`–`$EF` inaudible; `$F0`–`$FE` command; **`$FF`
  jump**, right = target/`$00` stop); right = note (`$00`–`$5F` rel up;
  `$60`–`$7F` rel down; `$80` keep; `$81`–`$DF` absolute C#0–B-7). Waveform bits:
  `$01` gate, `$02` sync, `$04` ring, `$08` test, `$10` tri, `$20` saw, `$40`
  pulse, `$80` noise.
- **Pulse table**: left `$01`–`$7F` modulation step (right = signed speed);
  `$8X`–`$FX` set pulse width; **`$FF` jump**.
- **Filter table**: left `$00` set cutoff (right = value); `$01`–`$7F`
  modulation (right = signed speed); `$80`–`$F0` set params (passband hi-nibble
  `$90` LP / `$A0` BP / `$C0` HP; right = resonance / channel mask); **`$FF`
  jump**.
- **Speed table** (shared vibrato / portamento / funktempo groove): **no jump
  markers**; funktempo = two alternating tempo bytes per row.

## Pitch / frequency table [G]

A note → 16-bit-frequency lookup (adjustable A-4 pitch / SID clock). No public
byte dump exists for the JCH/NP pitch table — read it from the `.d64` player.

## Editor capacities (ED3.04 / NP20.G4) [V]

32 instruments, 31 sub-tunes, 114 patterns (up to 96 rows each), single-channel
patterns, one pattern per voice per order-list step ([chordian.net][cap]).
Earliest JCH editors had **no sequences** (one long tracker note stream); the
order-list / sequence system was added over time. NP22-25 ships several
alternative players trading raster time vs. flexibility. Per-version table-size /
opcode deltas for V22–V25 are unconfirmed online. **[G]**

## Mapping to pyjch's recovered model

pyjch already discovers these same tables *in the packed tune* (per-tune,
relocated) via player-code idioms (see `pyjch/newplayer.py`,
`pyjch/v20player.py`). The editor export is the inverse: place the recovered
tables at the editor's addresses and write the `$0Fxx` pointer block.

| Editor table | pyjch recovered base |
| ------------ | -------------------- |
| wave col 1 / 2 (`$0FBC`) | `NewPlayerModel.wave_note_col` / `V20Bases.wave_ctrl` |
| filter table (`$0FC0`) | `V20Bases.filterprog` (groove at idx 0–1) |
| pulse table (`$0FC2`) | `V20Bases.pwprog` |
| instrument table (`$0FC4`) | `NewPlayerModel.instruments` |
| order lists (`$0FC6/8/A`) | `NewPlayerModel.subtune_table` → `orderlist_ptr` |
| seq vectors lo/hi (`$0FCC/E`) | `NewPlayerModel.patternptr_lo` / `patternptr_hi` |
| command table (`$0FD0`) | `V20Bases.cmdparam` |
| fine-tune / pitch (`$0FBA`) | `NewPlayerModel.pitch_table` |

## Write-side format specification (canonical export layout)

The implementable spec an exporter targets: an **unpacked editor `.prg`**, load
`$0F00`, init `$1000`, play `$1003`. Two parts — a **player-code prefix** and a
**data region** — with a `$0Fxx` pointer block bridging them. Addresses below are
the verified NP20/20.G4 canonical layout; **[P]** marks a value that is a
per-driver *parameter* to confirm from the NP22-25 `.d64` before it is trusted.

### Image envelope

```
$0F00 ┬─ player code (stock NP driver binary)          [P: exact bytes/length]
      │   ...
      ├─ $0FA6..$0FEE  header pointer block             (see below)
      │   ...
$1000 ┼─ init entry (JMP)                               [P]
$1003 ┼─ play entry (JMP)                               [P]
      │   player code continues ...
$18CB ┼─ DATA REGION begins (256-byte-aligned tables)
      ...
$2CCB ┴─ sequence data (grows upward)
```

The player-code prefix is **not synthesizable** and is (likely) copyrighted: an
exporter injects a stock NP22-25 driver binary sourced at runtime from the
release `.d64` — never committed to this repo (per the project no-copyrighted-
material rule). The exporter owns only the data region + pointer block.

### Header pointer block (`$0F00` page)

Write these 16-bit LE words (see the `$0Fxx` table above): `$0FA6` init-data
base (default tempo byte = `word[$0FA6] + 6`), `$0FBA` fine-tune/pitch, `$0FBC`
wave, `$0FC0` filter, `$0FC2` pulse, `$0FC4` instruments, `$0FC6/$0FC8/$0FCA`
order lists v1/v2/v3, `$0FCC/$0FCE` sequence-vector lo/hi, `$0FD0` command
table; ASCII version magic at `$0FEE` (`"20.G"` for NP20 — **[P]** for NP22-25).

### Data region layout (canonical bases)

| Table | Base | On-disk encoding |
| ----- | ---- | ---------------- |
| Wave note col | `$18CB` | `u8` stream (`$7E` hold / `$7F` jump; right-col semantics in the wavetable section) |
| Wave ctrl col | `$19CB` | `u8` stream, parallel to note col (jump target at a `$7F` row) |
| Filter table | `$1ACB` | 4 interleaved cols (value, step, dwell, next); idx 0–1 = groove |
| Pulse table | `$1BCB` | 4 interleaved cols (reset, step, dir+rate, next) |
| Instruments | `$1CCB` | 8 bytes/record **[P: field widths]** |
| Seq ptr lo/hi | `$1DCB`/`$1ECB` | `u8` per pattern; `seq_addr[i] = hi[i]<<8 \| lo[i]` |
| Command table | `$1FCB` | 2-col row-major; hi-nibble = type, `$E0` = tempo |
| Order lists v0/1/2 | `$20CB`/`$24CB`/`$28CB` | `(0x20+transpose, seq_index)` pairs, `$FF` end |
| Sequence data | from `$2CCB` | `(byte0, note)` event pairs, `$7F` end |

### Emit algorithm

1. Inject the player-code prefix `[$0F00 .. $18CB)` from the sourced driver.
2. Lay each table at its canonical base (256-byte aligned; order lists at `$400`
   intervals; sequences packed consecutively from `$2CCB`, recording each
   sequence's address into the lo/hi vector tables).
3. Encode every table per the byte rules in the sections above.
4. Fill the `$0F00`-page pointer words to the chosen bases; write the version
   magic; set the default tempo byte at `word[$0FA6]+6`.
5. Emit as a 2-byte-load-address `.prg` (`$00 $0F` + image).

Bounds to respect (ED3.04 capacities): ≤ 32 instruments, ≤ 31 subtunes, ≤ 114
patterns, ≤ 96 rows/pattern. Overflow is an export error, not a silent clamp.

## Open-source readers / writers of JCH editor files

The short list of code that actually reads or writes the JCH-Editor native song
format (as opposed to merely *detecting* or *playing* JCH tunes):

| Project | R/W | Notes |
| ------- | --- | ----- |
| **CheeseCutter** (0-series / v1.x) | **read + write** | A cross-platform port of the JCH Editor; its early "0-series" targeted JCH-Editor compatibility — "most JCH Editor files are compatible with the early versions." The fullest open-source JCH read/write. **v2.x dropped JCH compatibility** for its own packed `.ct` format. Player driver: `src/c64/player_v4.acme`. Repos: [theyamo/CheeseCutter][cc], mirror [localhost/CheeseCutter][ccm]. **[V]** |
| **SID Factory II** — `converter_jch.cpp` | **read** (import) | Imports a JCH NewPlayer `.prg` (gated to version string `"20.G"`) into SF2; the single best machine-readable NP20 format spec. **No JCH writer** (SF2 saves its own format). [source][sf2]. **[V]** |
| **pyjch** (this repo) | **read** (structural) | Recovers the per-tune tables (subtune/order/pattern/instrument/wave/pw/filter) statically; the only standalone Python JCH reader. An editor-native *writer* is the export target this doc scopes. |
| **GoatTracker v2** | — | Not a JCH file reader/writer (own `.sng`), but JCH-*derived* (built "to resemble JCH NewPlayer 21"); used above only as a structural model. [readme][gt2]. |
| **sidid / player-id** | detect only | 6502-code fingerprints that classify JCH NewPlayer versions; no table parsing. [sidid][sidid]. |

Not open source (C64 tools, [on csdb][rel]): the **JCH-Packer** (relocate/crunch
for release) and **Syndrom's JCH-depacker** (packed → editor format). No
`jch2sf2`, GoatTracker→JCH, or standalone command-line JCH converter was found.

## Open gaps to close from the release files

Resolved from `converter_jch.cpp` (NP20, verified from its read loops): the
`$0Fxx` offsets, `"20.G"` magic, the editor order-list `(0x20+transpose, seq)`
pair encoding + `$FF` terminator, the sequence `byte0>=$C0`=command / else
instrument / `$7F`=end rule, and the 2-column row-major command table (`$E0`
tempo). Still open, and only in the release binaries:

1. NP22-25 version-magic string and whether the `$0Fxx` offsets shifted vs NP20.
2. Table **geometry** — bytes-per-instrument and wave/pulse/filter row/column
   counts — is taken from the SF2 NP20 driver definition (`DriverInfo`); confirm
   it matches NP22-25, and whether editor records carry trailing (name) bytes
   beyond the 8 the player consumes.
3. Pitch-table bytes.
4. Exact packed→editor transpose mapping; loop-vs-stop restart beyond `$FF`.
5. Full Super-table opcode list; V22–V25 vs V1–V21 table/command deltas.

Tools: **JC64dis** disassembler for the `.d64`; the SF2 `converter_jch.cpp` as a
working NP20 parser to invert.

## Sources

- Release (docs.doc + d64): <https://csdb.dk/release/?id=100406> [rel]
- SF2 JCH converter (verified): <https://github.com/Chordian/sidfactory2/blob/master/SIDFactoryII/source/runtime/editor/converters/jch/converter_jch.cpp> [sf2]
- Codebase64 JCH 20.G4 file format (verified): <https://codebase64.com/doku.php?id=base:jch_20.g4_player_file_format> [cb64]
- GoatTracker v2 readme (JCH-derived structural model): <https://raw.githubusercontent.com/leafo/goattracker2/master/readme.txt> [gt2]
- Editor capacities: <http://chordian.net/c64editors.htm> [cap]
- sidid / player-id signatures: <https://github.com/cadaver/sidid/blob/master/sidid.cfg>, <https://github.com/WilfredC64/player-id>
- CheeseCutter (0-series JCH-Editor port; read+write): <https://github.com/theyamo/CheeseCutter>, mirror <https://github.com/localhost/CheeseCutter>, driver <https://raw.githubusercontent.com/theyamo/CheeseCutter/master/src/c64/player_v4.acme>
- CheeseCutter as a cross-platform port of JCH-Editor / 0-series JCH compatibility: <https://chipmusic.org/forums/topic/3753/cheesecutter-crossplatform-port-of-jcheditor>, <https://battleofthebits.com/lyceum/View/CheeseCutter>
- CSDb forum (raw prg, init=$1000/play=$1003): <https://csdb.dk/forums/index.php?roomid=10&topicid=5698>
- JCH version history: <https://blog.chordian.net/2018/06/29/from-jchs-special-collection/>
- JC64dis disassembler: <https://iceteam.itch.io/jc64dis>

[rel]: https://csdb.dk/release/?id=100406
[sf2]: https://github.com/Chordian/sidfactory2/blob/master/SIDFactoryII/source/runtime/editor/converters/jch/converter_jch.cpp
[cb64]: https://codebase64.com/doku.php?id=base:jch_20.g4_player_file_format
[gt2]: https://raw.githubusercontent.com/leafo/goattracker2/master/readme.txt
[cap]: http://chordian.net/c64editors.htm
[cc]: https://github.com/theyamo/CheeseCutter
[ccm]: https://github.com/localhost/CheeseCutter
[sidid]: https://github.com/cadaver/sidid/blob/master/sidid.cfg
