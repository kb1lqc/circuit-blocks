# Contributing to circuit-blocks

## What a block is

- A block is one reusable piece of circuit, such as a current monitor, with its ports, settings and worst-case analysis.
- Board projects import a block by its URL, the same way they import parts from the component library.
- Each block has a bench board (a test board that uses the block) and a generated design report.

## Setup

What you need: the same tools as the component library (Python 3.11+ with matplotlib, poppler, pcb 0.4.x, GitHub
CLI signed in), plus KiCad 10 for the schematic steps.

Put the two repos side by side in one folder:

```sh
git clone https://github.com/kb1lqc/circuit-blocks.git
git clone --branch v0.6.0 https://github.com/kb1lqc/zener-library.git zener-library   # version: ZL_VERSION in analysis/_zl.py
git -C circuit-blocks config core.hooksPath tools/git
```

- The library checkout must be a clean, tagged release. The version is in `analysis/_zl.py` as `ZL_VERSION`.
  Use the same version here.
- Keep the library outside this repo. A nested workspace is rejected by `tools/check_env.py`, so keep the library
  outside this repo.
- Do not edit the library checkout here. Library changes go through the library repo (see its `CONTRIBUTING.md`).
- The git hook (`core.hooksPath`) runs `tools/check_env.py` on every commit, and checks that schematics match the `.zen`
  files when a commit touches them.

## Submit a circuit block

Work on a branch. Do not push to `main`.

1. **Write the spec first.** Copy the structure in `docs/spec/blocks.md`. Cover purpose, operating conditions, ports,
   settings with valid ranges, the part list with the reason for each part, and the limits the block must meet.
   Put design values in `spec.md` and `params.json`, each with a reason. Do not guess a number.
2. **Check the parts exist in the library.** Every part must come from the library. Never type a datasheet
   number here. If a part is missing, add it to zener-library first, with its own review, and then bump the
   library version here.
3. **Write the block folder** `blocks/<Block>/`:
   - `<Block>.zen`: the module (ports, settings)
   - `reference.zen`: the reference instance values
   - `pcb.toml`: its library dependencies. Add them with `pcb add`, never by hand.
   - `analysis.py`: the worst-case analysis
   - `spec.md`: the spec from step 1
   Ports and naming: the standard table in `docs/spec/blocks.md`.
4. **Write the bench board** `benches/<Block>/`: `bench.zen`, `params.json` (the bench values, each with a basis).
5. **Build the bench.**

   ```sh
   pcb build -Dwarnings benches/<Block>/bench.zen
   ```

   Expected: no errors and no warnings.
6. **Run the analysis.** This regenerates the bench report in `benches/<Block>/docs/`.

   ```sh
   python3 blocks/<Block>/analysis.py
   ```

   Every requirement in the report needs a margin. A margin is the room left between the worst case and the limit.
7. **Update the schematic notes and PDF** (the bench reference sheet: the arranged KiCad schematic of the bench board,
   kept in sync with the `.zen`). Run each command on its own:

   ```sh
   python3 tools/schematic/update_notes.py benches/<Block>
   python3 tools/schematic/update_notes.py benches/<Block>
   tools/schematic/export_pdf.sh benches/<Block>
   tools/schematic/check_sync.sh
   ```

   Run `update_notes.py` twice. The second run must change nothing.

   `export_pdf.sh` looks for `kicad-cli` at `/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli` (macOS). On another
   system, set `KICAD_CLI=<path to kicad-cli>` first.

   Expected from `check_sync.sh`: a line `RESULT: IN SYNC - ...`. If it does not print that, the schematic and the `.zen`
   differ. Fix the `.zen` file, or regenerate the schematic, and run the check again.
   Commit only when `check_sync.sh` prints `IN SYNC`. The pre-commit hook enforces this. Never skip it with `--no-verify`.
8. **Run the full check.**

   ```sh
   python3 tools/verify.py [<bench>]
   ```

   Expected: one line that starts with `PASS`. If it fails, it prints the first 5 problems and a log path.
   For the schematic gates (the checks that the schematic builds, its netlist matches and it is in sync), run
   `python3 tools/verify.py --schematic benches/<Block>`.
9. **Open a pull request.** CI runs the same checks (see below). Name every item marked `UNVERIFIED` and every waiver
   (an accepted exception, with its reason) in the PR text.

Releases (version tags and `pcb publish`) are done by the maintainer. See `docs/spec/blocks.md`, "Versioning". Ask the
maintainer before you tag anything.

## What CI tells you about part data

CI runs `tools/verify.py` and the schematic gates for each bench. It also runs a **human review packet** step.

- The packet lists every library row that the analysis reads, and that has no current human check.
- The packet is a warning only. It never fails CI.
- The packet is uploaded as the CI artifact `human-review-packet`. There is one HTML page per block, plus a summary.
- A report line that says **`PASS (UNVERIFIED INPUTS)`** means: the analysis passed, but it used library rows that no
  person has checked yet. Treat the result as provisional until those rows are verified. Here "no person has checked"
  means: rows never checked, or rows whose check no longer matches the row or the datasheet PDF.

How to get rows verified: the rows are checked in the library repo. Read the library guide, section "Verify
parameters (the human check)":
https://github.com/kb1lqc/zener-library/blob/main/CONTRIBUTING.md#verify-parameters-the-human-check

Rows are checked and stamped only by a person, in the library repo. Agents never stamp.

Verified stamps show up here only after two things happen:

- the library rows are verified and a new library version is released, and
- `ZL_VERSION` in `analysis/_zl.py` is bumped here, together with the `pcb.toml` dependency versions (`pcb add`).

Until both happen, this repo's reports keep showing the unverified warning.

## Run the packet locally

From the repo root:

```sh
python3 tools/review_packet.py --out-dir <folder for the output>
```

- Add block names to limit it, for example `python3 tools/review_packet.py INA229HighSideMonitor --out-dir <folder>`.
- The library is found with `--library PATH`, or the `ZENER_LIBRARY` variable, or `../zener-library`.
- Output in the folder: `<Block>.html` (one page per block), `summary.md` and `annotations.txt`.
- The tool does not change the committed bench reports. It works in a temporary folder.
- Exit code 0: every packet was built. Exit code 1: a tool error. Read the message it prints.
