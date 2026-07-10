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

## Header pointer table `$0FA0` [V, NP20/21]

The editor keeps every table's base in a **contiguous 16-bit-LE pointer array
based at `$0FA0`**, indexed `ptr[i] = word[$0FA0 + i*2]`. This indirection is the
heart of the native format — the packed player inlines these operands, the editor
reads through the array. **Two independent readers agree byte-for-byte**: SID
Factory II ([`converter_jch.cpp`][sf2], as scattered `$0Fxx` reads) and
CheeseCutter 0.5.1 ([`src/vsong.d`][cc], as the `$0FA0`-indexed array — the
clearer model):

| `i` | Addr | Points at |
| --- | ---- | --------- |
| `2` | `$0FA4` | info string (song name at `+1`, 30 bytes) |
| `3` | `$0FA6` | init-data base; **default tempo byte = `ptr[3] + 6`** |
| `$0D` | `$0FBA` | fine-tune table |
| `$0E` | `$0FBC` | **wave table** (col 1 at `+0`, col 2 at `+256`) |
| `$10` | `$0FC0` | **filter table** (first 4 bytes = break-speed table) |
| `$11` | `$0FC2` | **pulse table** |
| `$12` | `$0FC4` | **instrument table** (256 bytes → 32 × 8-byte records) |
| `$13/$14/$15` | `$0FC6/8/A` | **order list** voice 0 / 1 / 2 (`$400` bytes each) |
| `$16` | `$0FCC` | **sequence-pointer LOW** table |
| `$17` | `$0FCE` | **sequence-pointer HIGH** table |
| `$18` | `$0FD0` | **super / command table** |
| — | `$0FEE` | **5** ASCII version bytes (`"20.G4"`, `"21.G5"`; `npversion = atoi(first 2)`) |

Sequence address: `seq_addr[i] = (hi_table[i] << 8) | lo_table[i]` (up to `$68`
sequences, `$60` rows each, 256-byte span). Tempo special case: if the default
tempo byte `< 2` (break-speed / funktempo), real values come from the filter
table's first entry (`filter+1` / `filter+0`).

> NP22-25: the array layout is stable across 20.g4 → 21.g5 (Laxity kept it
> deliberately, see below); **confirm only the version-magic string** (likely
> `"22.G*"`–`"25.G*"`) and any late index additions against the release `.d64`.
> **[G]**

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

Two columns, **row-major** on disk (`col2 = col1 + row_count`), from
[`converter_jch.cpp`][sf2]. A `$C0`–`$DF` sequence byte indexes it; each entry is
a `(cmd, param)` pair. Full opcode list, verified from Laxity's NP21 spy spec
([`21.g5_Final.txt`][cc], "same as 20.g4" for the shared ones):

| entry `cmd param` | effect |
| ----------------- | ------ |
| `0x yy` | slide up, speed `$xyy` |
| `2x yy` | slide down, speed `$xyy` |
| `4x yy` (`40`–`5f`) | invoke instrument `x` (`0`–`$1F`) with alt wave-table pointer `yy` |
| `60 xy` | vibrato, `x` = frequency, `y` = amplitude |
| `8x xx` | portamento, speed `$xxx` |
| `9x yy` | set AD-`x` / SR-`yy` (persistent) |
| `ax yy` | set AD-`x` / SR-`yy` directly (one-shot) |
| `c0 xx` | set channel wave pointer directly to `xx` |
| `dx yy` | `x=0` filter-table ptr `yy`; `x=1` absolute filter value; `x=2` pulse-table ptr |
| `e0 xx` | set speed `xx` |
| `f0 xx` | set master volume `xx` |

`$1x`/`$3x`/`$7x`/`$bx` are unassigned. NP20's `$C0`-bucket classification (used
by `v20player`/`extract`) is the 20.g4 dialect of this same table. **[G]** V22–V25
may add opcodes — confirm from `NP22-25 docs.doc`.

## Instrument record (8 bytes) [V]

Verified from Laxity's NP21 spec ([`21.g5_Final.txt`][cc], stated "same layout as
20.g4") and CheeseCutter's loader ([`vsong.d`][cc], `instrtbl[i*8 + n]`); the
256-byte table holds up to **32** records. This matches what `pyjch/extract.py`
already decodes from this repo's own RE:

| off | field |
| --- | ----- |
| +0 | **AD** (attack/decay) |
| +1 | **SR** (sustain/release) |
| +2 | **restart / wave-count**: low nibble = wave-table dwell; high nibble = restart mode (`$8x` hard, `$4x` soft/gate-off, `$2x` Laxity hard [needs bit7], `$1x` wave-generator reset; `$8x`+`$1x` combine; `$00` = gate off 3 frames before next note) |
| +3 | **filter setting**: low nibble = pass band, high nibble = resonance (non-zero enables filter on the channel) |
| +4 | **filter-table pointer** (`$00` + non-zero +3 → static filter, table routine off) |
| +5 | **pulse-table pointer** |
| +6 | **pulse property**: bit0 = pulse table reset only on instrument-set; bit1 = filter table reset only on instrument-set |
| +7 | **wave-table pointer** |

**20.g4 → 21 delta [V]:** 20.g4 additionally used byte +2 **bit6 = absolute-freq
(drum) mode** and a second wave-start copy at +6/+7 for note-off; NP21 dropped both
(21 fixed the tie-note handling differently). `v20player`/`extract` decode the
**20.g4** dialect (bit6 abs-freq, +6/+7 = `wstart`/`wstart2`) — correct for the
V20-build tunes they target; an NP21/22-25 emitter uses the +6 pulse-property /
+7 wave-pointer layout above.

## Wave / pulse / filter tables [V]

From `21.g5_Final.txt`; wave = two 256-byte columns, pulse/filter = 4 bytes/entry.

- **Wave table** — 2 bytes/tick `(xx=note, yy=waveform)`: `xx` bit7 set = absolute
  note (transpose not applied); `xx == $80` = special (recompute base note +
  transpose, for Hubbard-slide effects); `xx == $7F` = **jump** (`yy` = target
  index); `xx == $7E` = **stop** (hold last entry). Slide/vibrato apply only where
  `xx == $00`.
- **Pulse table** — 4 bytes/entry `(xx, yy, zz, qq)`: `xx` = pulse value (hi
  nibble = PW low part, lo nibble = PW high part; `$FF` = keep current); `yy` =
  count; `zz` = duration bits0-6 + direction bit7; `qq` = **next entry (absolute
  index)**.
- **Filter table** — 4 bytes/entry `(xx, yy, zz, qq)`: **the first entry is the
  break-speed table** (see below); otherwise `xx` = filter value (`$FF` = keep);
  `yy` = count; `zz` = duration; `qq` = next entry (absolute index).

## Speed / break-speed table [V]

Speeds `$00`/`$01` are "break speeds": the player looks the real speed up in the
**filter table's first entry** (≤ 4 values, wrap on a `$00` byte); `$01` clamps to
`$02`. This is the shared vibrato / portamento / funktempo groove source. **[G]**
the note→16-bit-frequency **pitch table** bytes are engine-internal and still
undumped — read from the `.d64` player.

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
| **CheeseCutter 0.5.1** (0-series) | **read + write** | A cross-platform port of the JCH Editor: `src/vsong.d` `open()`/`save()` read *and write* native `.prg` files at `$0F00`, and it **bundles Laxity's NP21 format spec** `doc/21.g5_Final.txt` — the primary source for the tables/opcodes above. The fullest open-source JCH read/write. **v2.x dropped JCH** for its own packed `.ct`. Source tarball [`ccutter-0.5.1.tar.gz`][cc]; live repo [theyamo/CheeseCutter][ccgit] is 2.x. **[V]** |
| **SID Factory II** — `converter_jch.cpp` | **read** (import) | Imports a JCH NewPlayer `.prg` (gated to `"20.G"`) into SF2; cross-confirms the pointer array + order/sequence/command encodings. **No JCH writer**. [source][sf2]. **[V]** |
| **pyjch** (this repo) | **read** (structural) | Recovers the per-tune tables (subtune/order/pattern/instrument/wave/pw/filter) statically; the only standalone Python JCH reader. An editor-native *writer* is the export target this doc scopes. |
| **GoatTracker v2** | — | Not a JCH file reader/writer (own `.sng`), but JCH-*derived* (built "to resemble JCH NewPlayer 21"); used above only as a structural model. [readme][gt2]. |
| **sidid / player-id** | detect only | 6502-code fingerprints that classify JCH NewPlayer versions; no table parsing. [sidid][sidid]. |

Not open source (C64 tools, [on csdb][rel]): the **JCH-Packer** (relocate/crunch
for release) and **Syndrom's JCH-depacker** (packed → editor format). No
`jch2sf2`, GoatTracker→JCH, or standalone command-line JCH converter was found.

## Open gaps to close from the release files

Two independent primary sources — SF2 `converter_jch.cpp` and CheeseCutter 0.5.1
(`vsong.d` + the bundled `21.g5_Final.txt`) — now **agree** on and fix: the
`$0FA0` pointer array, the 5-char version magic, the **8-byte instrument record**,
the wave/pulse/filter byte encodings, the full super-command list, the order-list
`(0x20+transpose, seq)` pairs, and the sequence `byte0>=$C0`/`$7F` rule. What is
still genuinely open, only in the release binaries:

1. NP22-25 exact **version-magic string** (likely `"22.G*"`–`"25.G*"`) and any
   opcode/field deltas the NP22-25 manual (`NP22-25 docs.doc`) adds over NP21.
2. The **pitch table** bytes (engine-internal; not dumped by either reader).
3. The precise **packed→editor** transform the closed-source JCH-Packer applies
   (pyjch recovers the packed runtime form; the editor form is documented above).

Tools: **JC64dis** disassembler for the `.d64`; CheeseCutter 0.5.1 `vsong.d` and
SF2 `converter_jch.cpp` as two working readers to invert.

## Sources

- Release (docs.doc + d64): <https://csdb.dk/release/?id=100406> [rel]
- SF2 JCH converter (verified): <https://github.com/Chordian/sidfactory2/blob/master/SIDFactoryII/source/runtime/editor/converters/jch/converter_jch.cpp> [sf2]
- Codebase64 JCH 20.G4 file format (verified): <https://codebase64.com/doku.php?id=base:jch_20.g4_player_file_format> [cb64]
- GoatTracker v2 readme (JCH-derived structural model): <https://raw.githubusercontent.com/leafo/goattracker2/master/readme.txt> [gt2]
- Editor capacities: <http://chordian.net/c64editors.htm> [cap]
- sidid / player-id signatures: <https://github.com/cadaver/sidid/blob/master/sidid.cfg>, <https://github.com/WilfredC64/player-id>
- **CheeseCutter 0.5.1 source (verified; `src/vsong.d` reader/writer + bundled `doc/21.g5_Final.txt` NP21 spec by Laxity)**: release <https://csdb.dk/release/?id=102245>, tarball <http://csdb.dk/getinternalfile.php/136516/ccutter-0.5.1.tar.gz>, JCH depacker <http://csdb.dk/getinternalfile.php/136517/depack-jch20g4.zip>
- CheeseCutter live repo (2.x, JCH dropped) / port history: <https://github.com/theyamo/CheeseCutter>, <https://chipmusic.org/forums/topic/3753/cheesecutter-crossplatform-port-of-jcheditor>
- CSDb forum (raw prg, init=$1000/play=$1003): <https://csdb.dk/forums/index.php?roomid=10&topicid=5698>
- JCH version history: <https://blog.chordian.net/2018/06/29/from-jchs-special-collection/>
- JC64dis disassembler: <https://iceteam.itch.io/jc64dis>

[rel]: https://csdb.dk/release/?id=100406
[sf2]: https://github.com/Chordian/sidfactory2/blob/master/SIDFactoryII/source/runtime/editor/converters/jch/converter_jch.cpp
[cb64]: https://codebase64.com/doku.php?id=base:jch_20.g4_player_file_format
[gt2]: https://raw.githubusercontent.com/leafo/goattracker2/master/readme.txt
[cap]: http://chordian.net/c64editors.htm
[cc]: http://csdb.dk/getinternalfile.php/136516/ccutter-0.5.1.tar.gz
[ccgit]: https://github.com/theyamo/CheeseCutter
[sidid]: https://github.com/cadaver/sidid/blob/master/sidid.cfg
