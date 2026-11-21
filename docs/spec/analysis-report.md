# Design-analysis report: format contract

Applies to every bench's generated datasheet (`benches/<Block>/docs/design-analysis.md`, `.pdf`, charts, `values.json`). Engineering rules are in the `ee-design` skill.

## Generation
- A Python script in jupytext percent format (`# %%` cells) regenerates, with one command, the committed Markdown datasheet and PNG charts in `docs/`, `values.json`, and `docs/design-analysis.pdf` on every run. No raw `.ipynb`; no hand-written numbers.
- The PDF carries a hash of the Markdown it was made from; the sync check (`check_sync.sh`) fails when it is stale or missing. Never hand-edit it.
- `tools/check_reports.py` regenerates each bench's `design-analysis.md` and `values.json` in a temp copy and fails on any byte difference, a missing or empty `values.json`, or zero benches. The PDF is not byte-reproducible and is covered by the hash check only.
- `tools/review_packet.py --out-dir DIR` runs each analysis into a temp folder and writes, per block, the library rows it read that lack a current human stamp (`human:<name>`, library `docs/spec/review.md`) as `DIR/<Block>.html` with the review evidence; CI flags them as warnings and uploads the packets (artifact `human-review-packet`), never a failure.
- The datasheet is pure analysis of the design as it is: no options, alternatives, candidate comparisons or worked examples. Option studies happen earlier in scratch scripts.

## Contents
1. Summary table: result, limit, margin, PASS / FAIL / ESTIMATE.
2. Operating conditions and assumptions.
3. Worst-case performance of every measured or regulated quantity over its range, with charts (error-vs-operating-point bands, contributor breakdown).
4. Thermal: dissipation vs rating over current and ambient (derating curve), self-heating and its effect on accuracy (temperature coefficients at the self-heated temperature), junction temperatures.
5. Quiescent current and power budget per supply (active and shutdown).
6. Ratings and abs-max margins.
7. Firmware constants.
8. Datasheet design-example walk-through (below).
9. Limits of this analysis (DC steady state, 25 C-only rows, curve estimates).
Curve-derived values are labelled estimates.

## Error stack-up
Every accuracy result has a table per case (uncalibrated / calibrated, full scale / low end): each contributor isolated, with its cite or design input, min/max, the corner and sign giving its worst case, sorted largest first; plus a bar chart of the same. The judged total is the worst case over all corners of all inputs together: not the linear sum (cross products add), not RSS (RSS is information only). For a non-multilinear function (ratios, quadratic in temperature), add a sampling check that random draws stay inside the corner bounds.

## Datasheet design examples
When a part's datasheet has an application or design-procedure example (bootstrap capacitor, current-limit resistor, timer capacitor, UVLO/OV divider, inrush), the analysis has a section that repeats each step in the datasheet's order with this design's values: the equation and its cite, the datasheet example's inputs and result beside this design's inputs, nominal and worst-case results, and the chosen standard value. Library `EXAMPLES` already prove the equation; this section shows the procedure applied.

## Required numbers
The report must contain every key that `benches/<Block>/notes_rules.py` references (`update_notes.py` refuses a missing key), and the per-block list in `docs/spec/blocks.md`, "Block spec and report contents".
