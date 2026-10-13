# The typed song (`pyjch.song`)

`decompile_sid(src).song` is a `JchSong`: frozen dataclasses in the NewPlayer's
own terms, with no addresses and no memory image. Bits a build ignores are kept
in `reserved`, so `encode(song)` reproduces the song data `decode(layout)` read,
table by table (`SongData`).

| part | type | bytes |
| --- | --- | --- |
| subtune | `Subtune(tempo, flags, tracks)` | the record's tempo and flag bytes; one `Track` per voice |
| track | `Track(ops, end)`; ops `PlaySequence(sequence)`, `Transpose(semitones)`; end `Loop()` / `Stop()` | `<$80` sequence, `$80+n` transpose, `$FF` loop, `$FE` stop (V0x: `$80+(n+12)`, `$FF`) |
| sequence | `Sequence(rows)`; `Row(prefix, event)` | rows up to `$7F` (V0x `$FF`) |
| row prefix | `Duration(rows, tie)`, `SelectInstrument(instrument)`, `RunCommand(command)` | `$80` + tie `$10` + rows, `$A0+i`, `$C0+k` (V0x: `$80+rows`) |
| row event | `Note(pitch)`, `GateOff()`, `Hold()` | `$01-$7D`, `$00`, `$7E` (V0x `$7F`) |
| command | `Slide(down, speed)`, `Vibrato(speed, delay, depth)`, `SetSustainRelease(value)`, `SetTempo(value)`, `SetVolume(value)`, `SetInstrumentWave(instrument, wave)` | two bytes: class `$0x/$2x`, `$6x`, `$9x`, `$Ex`, `$Fx`, else |
| instrument | `Instrument(attack_decay, sustain_release, hard_restart, absolute, wave_speed, filter_resonance, filter_mode, filter_program, pulse_program, wave, wave_release)` | eight bytes |
| wave table | `WaveStep(wave, note, absolute)`, `WaveHold(wave)`, `WaveJump(row)` | note column / waveform column; `$80+n` absolute, `$7E` hold, `$7F` jump |
| pulse table | `PulseStep(width, add, down, frames, next)` | four bytes; `$FF` keeps the width |
| filter table | `FilterTable(steps)` of `FilterStep(cutoff, add, frames, next)`; `.groove`, `.voice` from the first record | four bytes; `$FF` keeps the cutoff |
| pitch | `tuple[int]` | 16-bit LE frequencies |

Table extents: the sequences the tracks play; instruments and commands up to the
highest one referenced; the pulse and filter records reachable from the
instruments through record-aligned `next` links; the wave rows between the two
columns (or reachable from the instruments); the stored tuning.

Build facts are not song: `JchInfo` (version, host, code base, voices, V20
tables and work cells, wrapper cells, `V0xBuild` gate/envelope bytes) and
`DecompiledSid.layout`, the located model `JchPlayer` runs.
