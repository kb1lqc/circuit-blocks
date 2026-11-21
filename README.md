# circuit-blocks

[![verify](https://github.com/kb1lqc/circuit-blocks/actions/workflows/verify.yml/badge.svg)](https://github.com/kb1lqc/circuit-blocks/actions/workflows/verify.yml) [![INA229HighSideMonitor](https://img.shields.io/github/v/tag/kb1lqc/circuit-blocks?filter=blocks/INA229HighSideMonitor/*&label=INA229HighSideMonitor)](https://github.com/kb1lqc/circuit-blocks/tags) [![TPS48000HighSideSwitch](https://img.shields.io/github/v/tag/kb1lqc/circuit-blocks?filter=blocks/TPS48000HighSideSwitch/*&label=TPS48000HighSideSwitch)](https://github.com/kb1lqc/circuit-blocks/tags)

Reusable circuit blocks designed in [Zener](https://docs.pcb.new) with Diode's `pcb` toolchain. Each block is a
versioned package that board projects import by URL, the same way they import parts from
[kb1lqc/zener-library](https://github.com/kb1lqc/zener-library). Every block comes with a worst-case analysis that a
board runs for its own instance values, a bench board, and an arranged reference schematic sheet.

## Blocks

| Block | What | Bench reference instance |
|---|---|---|
| [`INA229HighSideMonitor`](blocks/INA229HighSideMonitor/) | INA229 high-side current, bus voltage, power and energy monitor with on-block Kelvin shunt, SPI | 12-40 V bus, 6 A full scale ([analysis](benches/INA229HighSideMonitor/docs/design-analysis.md), [schematic PDF](benches/INA229HighSideMonitor/docs/INA229HighSideMonitor_bench.pdf)) |

## Layout

| Path | What |
|---|---|
| `blocks/<Block>/` | One `pcb` package per block: `<Block>.zen`, `pcb.toml` (its library dependencies), `spec.md`, `analysis.py` |
| `benches/<Block>/` | Bench board `bench.zen`, `params.json` (instance values for the analysis), `eda/` (arranged sheets; `eda/<instance>.kicad_sch` is the reference sheet), generated `docs/` (analysis and `<bench name>.pdf`), `notes_rules.py` |
| `analysis/` | Shared Python helpers (`_zl.py` loads the component library) |
| `tools/` | `schematic/check_sync.sh`, `export_pdf.sh`, `update_notes.py`, `stamp_block.py`; `git/pre-commit` |
| `docs/` | Repository notes; `docs/datasheets/` and `docs/vendor/` are git-ignored |

## Setup

```sh
# pcb toolchain 0.4.x: https://docs.pcb.new
git clone https://github.com/kb1lqc/circuit-blocks.git
# Python analyses need the library checked out next to this repo, at the version pinned in analysis/_zl.py:
git clone --branch v0.6.0 https://github.com/kb1lqc/zener-library.git zener-library
git -C circuit-blocks config core.hooksPath tools/git   # schematic sync check on commit
```

The `.zen` files are the source of truth for the circuit; review schematics in KiCad, but make circuit changes in the
`.zen`. The library checkout must stay outside this repo: `pcb` rejects any nested `pcb.toml` with a `[workspace]`
section. Set `ZENER_LIBRARY` to use a different checkout.

Setup and submitting a block, step by step: [CONTRIBUTING.md](CONTRIBUTING.md).

## Use (from the repo root)

```sh
pcb build -Dwarnings benches/INA229HighSideMonitor/bench.zen      # build and check a bench
python3 blocks/INA229HighSideMonitor/analysis.py                   # regenerate the bench analysis
python3 tools/schematic/update_notes.py benches/INA229HighSideMonitor   # schematic notes from the analysis
tools/schematic/export_pdf.sh                                      # schematic PDFs into benches/*/docs/
tools/schematic/check_sync.sh                                      # schematics match the .zen; PDFs up to date
```

## Using a block on a board

```sh
pcb add github.com/kb1lqc/circuit-blocks/blocks/INA229HighSideMonitor@<version>
```

```zen
INA229HighSideMonitor = Module("github.com/kb1lqc/circuit-blocks/blocks/INA229HighSideMonitor/INA229HighSideMonitor.zen")
```

Run the block's analysis for each instance from a checkout of this repo at the same tag
(`python3 blocks/<Block>/analysis.py --params <instance.json> --out <board docs dir> --instance <NAME>`), and stamp
the reference sheet onto the board per instance with `tools/schematic/stamp_block.py`.
