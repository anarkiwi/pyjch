# pyjch

Pure-Python reader and player for **JCH NewPlayer** C64 SID songs (the JCH /
Jens-Christian Huus tracker player used across the demoscene), with byte-exact
per-frame SID register output and a register-log surface for downstream tooling.

`decompile_sid` finds the NewPlayer core by its own `JMP init ; JMP play` vectors
wherever it sits and whatever drives it (the PSID driver, a host routine around
it, its own interrupt, or a crunched image's init, run on jennings), and returns
a typed song model, the build it was read from, and the header. `JchPlayer`
replays the V0x and V20 cores with native engines and every other build by
running its own code via `pysidtracker.EmuPlayer`. See
[docs/versions.md](docs/versions.md) for the HVSC census.

## Install

```bash
pip install pyjch
```

## Usage

```python
import pyjch

dec = pyjch.decompile_sid("tune.sid")   # path, bytes, or binary file object; .sid or .prg
dec.song                                 # the typed song (docs/song.md)
dec.info                                 # version, host, code base, voices, tables
song = dec.layout                        # the located model the player runs

# Per-frame SID register writes (changed registers only, after frame 0).
for writes in pyjch.iter_frames(song, max_frames=50 * 60):
    ...                                  # writes: list[(register, value)]
```

Installing pyjch also registers a `jch` format with the shared `pysidtracker`
command (no separate `pyjch` binary): `pysidtracker info/reglog/wav tune.sid`,
plus a JCH `pysidtracker export`.

See [docs/song.md](docs/song.md) for the typed song, [docs/usage.md](docs/usage.md) for the register grid, register logs, and the
CLI, [docs/format.md](docs/format.md) for the format, players, and byte-exact
validation, [docs/versions.md](docs/versions.md) for the HVSC census, and
[docs/editor-format.md](docs/editor-format.md) for the JCH-Editor NP22-25 native
song layout (the structural-export target).

## Development

```bash
pip install -e ".[dev]"
./run_tests.sh        # black + pylint + pytest with coverage
```

## License

Apache 2.0 — see [`LICENSE`](LICENSE).
