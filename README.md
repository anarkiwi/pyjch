# pyjch

Pure-Python reader and player for **JCH NewPlayer** C64 SID songs (the JCH /
Jens-Christian Huus tracker player used across the demoscene), with byte-exact
per-frame SID register output and a register-log surface for downstream tooling.

Consumes `.sid` files (PSID/RSID containers) and bare `.prg` images through the
shared [`pysidtracker`](https://github.com/anarkiwi/pysidtracker) base: the
per-tune immediates and table bases are discovered by matching the surrounding
player-code instruction bytes (relocation-safe), and packed/relocating tunes are
detected rather than mis-parsed — container headers are not trusted.

`parse` returns a byte-exact-replayable `Song` for the canonical V0x layout, a
`NewPlayerModel` for the wider wavetable family (~2,000 HVSC tunes), and cleanly
rejects the genuinely different players and packed/relocated rips. The V20
two-column engine (largest HVSC bucket) is byte-exact via
`pyjch.v20player.V20Player`. See [docs/versions.md](docs/versions.md) for the
per-version HVSC census.

## Install

```bash
pip install pyjch
```

## Usage

```python
import pyjch

song = pyjch.read("tune.sid")            # path, bytes, or binary file object; .sid or .prg

# Per-frame SID register writes (changed registers only, after frame 0).
for writes in pyjch.iter_frames(song, max_frames=50 * 60):
    ...                                  # writes: list[(register, value)]
```

See [docs/usage.md](docs/usage.md) for the register grid, register logs, and the
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
