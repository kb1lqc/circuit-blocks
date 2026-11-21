#!/usr/bin/env python3
"""Human-review packet: per block, the library rows its analysis reads that lack a current human stamp.

Usage (repo root):
  python3 tools/review_packet.py [BLOCK ...] --out-dir DIR [--library PATH] [--root R]
BLOCK is a blocks/<Block> name; none given = every blocks/*/analysis.py. Library: --library, $ZENER_LIBRARY or
../zener-library (the same rule as tools/check_env.py).

Each analysis runs in a subprocess with `--out <temp>` (committed bench docs are never rewritten); afterwards the
library's record of rows read is collected: lib.human_review_needed() when the library has it, else (older releases)
lib.used_inputs() with every row listed (no stamp / machine stamp / no review record: a human stamp cannot be checked).
Writes DIR/<Block>.html (one self-contained page; evidence PNGs embedded from the library's git-ignored
.review-cache/<Mfr>/<Value>/, else an older release's committed review/; "no image (render locally)" when neither),
DIR/summary.md (also printed) and DIR/annotations.txt (one GitHub `::warning::` line per block).

Order in a packet: "fix before stamping" (DISAGREE, NOT_FOUND, review record older than the committed row); tier 1
auto row crop + AGREE_TEXT_CHECK; tier 2 auto row crop UNSURE; tier 3 table/page fallback (a fallback crop shorter than
MIN_FALLBACK_PT is replaced by the page image when the evidence has one); "page lookup" (no usable image: open the
page); "no review record in this library release" (no review/ dir, no ledger row, NOT_COVERED, catalog rows).
Flags are never failures. Exit 0 = every packet built, 1 = tool error (an analysis failed or crashed, no blocks, no
library); a failed block gets its own ::warning:: line and the other blocks are still written.
"""

import argparse
import base64
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

# Runs inside the analysis subprocess: run the script as __main__, then dump the library's input record.
DRIVER = r'''
import json, runpy, sys
script, out, dump = sys.argv[1:4]
sys.argv = [script, "--out", out]
try:
    runpy.run_path(script, run_name="__main__")
except SystemExit as e:
    if e.code not in (None, 0):
        raise
import lib
if hasattr(lib, "human_review_needed"):
    rows, api = lib.human_review_needed(), True
else:
    un = set(lib.unverified_inputs())
    rows, api = [{"input": k, "unverified": k in un} for k in lib.used_inputs()], False
json.dump({"used": lib.used_inputs(), "rows": rows, "api": api}, open(dump, "w"))
'''

TIERS = ("fix", "tier 1", "tier 2", "tier 3", "page lookup", "no record")
TITLES = {"fix": "Fix before stamping",
          "tier 1": "Tier 1: auto row crop, text check agrees",
          "tier 2": "Tier 2: auto row crop, text check unsure",
          "tier 3": "Tier 3: table or page image (find the row yourself)",
          "page lookup": "Page lookup: no usable image (open the page link)",
          "no record": "No review record in this library release"}
STAMPABLE = ("tier 1", "tier 2", "tier 3", "page lookup")
# A fallback crop shorter than this (PDF points; px * 72 / dpi) holds only a table title/header band, not a data row:
# library evidence 2026-11-20: heading-only crops were 15-60 pt (INA229 table_p5_y73 33 pt, ISC035 table_p5_y127 60 pt),
# the shortest crop showing a data row 66 pt (TPS48000 table_p5_y374).
MIN_FALLBACK_PT = 64
HINT_RE = re.compile(r"graph|curve|±", re.I)  # in the row's note or cite lines
WHERE = ("Run in your zener-library working copy (tools/stamp.py; a library release after v0.6.0, or the diodecomputer "
         "branch), after checking the image:")


def zl_dir(root, library=None):
    return Path(library or os.environ.get("ZENER_LIBRARY") or Path(root).parent / "zener-library").resolve()


def blocks(root, names):
    found = sorted(p.parent.name for p in (Path(root) / "blocks").glob("*/analysis.py"))
    return [n for n in found if n in names] + [n for n in names if n not in found] if names else found


def read_toml(path):
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return None


def row_hash(table):
    """The ledger's row hash (docs/spec/review.md): SHA-256 of the row table without `verified`, sorted compact JSON."""
    t = {k: v for k, v in table.items() if k != "verified"}
    return hashlib.sha256(json.dumps(t, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def png_height(path):
    """Pixel height from the PNG IHDR chunk (None when not a PNG)."""
    try:
        b = Path(path).read_bytes()[:24]
    except OSError:
        return None
    return int.from_bytes(b[20:24], "big") if b[:8] == b"\x89PNG\r\n\x1a\n" and b[12:16] == b"IHDR" else None


def evidence(d, rel):
    """A review image: the library's git-ignored cache <library>/.review-cache/<Mfr>/<Value>/<rel> (current releases),
    else <component>/review/<rel> (older releases committed images); None when neither exists (render locally)."""
    d = Path(d)
    for base in (d.parents[2] / ".review-cache" / d.parent.name / d.name, d / "review"):
        if rel and (base / rel).is_file():
            return base / rel
    return None


def url_takes_page(url):
    """True when url#page=N is likely to open the PDF at that page: a direct .pdf, a TI /lit/ link or a doc? handler."""
    return bool(url) and (url.lower().split("?")[0].endswith(".pdf") or "/lit/" in url or "doc?" in url)


def toml_block(path, header):
    """The source lines of one TOML table, from its header to the next header ('' when absent); no `verified` line."""
    try:
        lines = Path(path).read_text().split("\n")
    except OSError:
        return ""
    start = next((i for i, ln in enumerate(lines) if ln.strip() == header), None)
    if start is None:
        return ""
    end = next((i for i in range(start + 1, len(lines)) if lines[i].lstrip().startswith("[")), len(lines))
    while end > start + 1 and (not lines[end - 1].strip() or lines[end - 1].lstrip().startswith("#")):
        end -= 1  # trailing comments belong to the next table
    return "\n".join(ln for ln in lines[start:end] if not re.match(r"\s*verified\s*=", ln))


def locate(zl, label, key):
    """Fallback for a library without human_review_needed: (dir, review_key, stamp, cite) of '<label>:<key>'."""
    base, sel = label.split("[")[0], "[" in label
    comps = sorted(p.parent for p in (zl / "components").glob(f"*/{base}/component.toml"))
    variant = None
    if not comps:
        for v in sorted((zl / "components").glob("*/*/variants.toml")):
            if base in (read_toml(v) or {}).get("variant", {}):
                comps, variant = [v.parent], base
                break
    if len(comps) != 1:
        return None, None, None, None
    d = comps[0]
    own = ((read_toml(d / "variants.toml") or {}).get("variant", {}).get(variant, {}).get("param", {})
           if variant else {})
    if key in own:
        return d, f"variant.{variant}.{key}", own[key].get("verified"), own[key].get("cite")
    t = (read_toml(d / "component.toml") or {}).get("param", {}).get(key)
    if t is None or (sel and key in ("capacitance", "rated_v")):
        return d, None, None, None
    return d, key, t.get("verified"), t.get("cite")


def normalise(zl, data):
    """Rows needing review as dicts with input, value, key, dir, review_key, ref, stamp, state, cite, reason."""
    if data["api"]:
        return data["rows"]
    out = []
    for r in data["rows"]:
        label, _, key = r["input"].rpartition(":")
        d, rk, stamp, cite = locate(zl, label, key)
        reason = ("no stamp" if r["unverified"] else "no review record" if str(stamp or "").startswith("human:")
                  else "machine stamp")  # an old release cannot check a human stamp's hashes: listed (fail safe)
        ref = f"{d.name}:{rk}" if d and rk and not rk.startswith("variant.") else None
        out.append({"input": r["input"], "value": label, "key": key, "dir": str(d) if d else None, "review_key": rk,
                    "ref": ref, "stamp": stamp, "state": None, "cite": cite, "reason": reason})
    return out


def classify(item):
    """Attach the ledger row and evidence; set item['tier'] and item['why'] (empty when nothing to say)."""
    d = Path(item["dir"]) if item.get("dir") else None
    rk = item.get("review_key")
    led = read_toml(d / "review" / "review.toml") if d else None
    item["ledger"], item["row"] = led, None
    if d and rk:
        header = (f'[variant.{rk.split(".")[1]}.param.{rk.split(".", 2)[2]}]' if rk.startswith("variant.")
                  else f"[param.{rk}]")
        item["toml"] = toml_block(d / ("variants.toml" if rk.startswith("variant.") else "component.toml"), header)
    else:
        item["toml"] = ""
    notes = [ln for ln in item["toml"].split("\n") if re.match(r"\s*(note|cite)\s*=", ln)] + [str(item.get("cite") or "")]
    item["hint"] = bool(HINT_RE.search(" ".join(notes)))
    row = (led or {}).get("row", {}).get(rk) if rk else None
    if not isinstance(row, dict) or row.get("state") == "NOT_COVERED" or not row.get("hash"):
        item["tier"] = "no record"
        item["why"] = (row or {}).get("reason") or ("no review/ in this library release" if not led
                                                    else "catalog row, not reviewed" if not rk else "no ledger row")
        return item
    item["row"] = row
    table = (read_toml(d / "component.toml") or {}).get("param", {}).get(rk, {})
    if row.get("status") in ("DISAGREE", "NOT_FOUND"):
        item["tier"], item["why"] = "fix", f"status {row['status']}: {row.get('reason', '')}"
    elif row["hash"] != row_hash(table):
        item["tier"], item["why"] = "fix", "review record older than the committed row: re-run the library's tools/verify.py"
    elif row.get("crop") == "auto":
        item["tier"] = "tier 1" if row.get("status") == "AGREE_TEXT_CHECK" else "tier 2"
        item["why"] = row.get("reason", "")
        item["images"] = [(row.get("header_image"), "table header"), (row.get("image"), "row"),
                          (row.get("foot_image"), "footnotes")]
    else:
        item["why"] = row.get("reason", "")
        rel = row.get("image")
        found = evidence(d, rel)
        h = png_height(found) if found else None
        absent = rel and found is None  # not rendered on this machine: height unknown, keep it (shown as "no image")
        tall = absent or (h is not None and h * 72 / (row.get("dpi") or 150) >= MIN_FALLBACK_PT)
        page = f"evidence/page_p{row.get('pdf_page')}.png" if row.get("pdf_page") else None
        page = page if page and page != rel and evidence(d, page) else None
        item["images"] = [(rel, "table or page")] if tall else []
        if page:
            item["images"].append((page, "full page"))
        item["tier"] = "tier 3" if item["images"] else "page lookup"
        if not tall and rel and not absent:
            item["why"] += f" [crop {rel} too short to hold a row ({h} px at {row.get('dpi')} dpi); not shown]"
    item["images"] = [(r, a) for r, a in item.get("images", []) if r]
    return item


def page_html(item):
    """Where to look: local file, PDF page, printed page, and the URL (with #page only when url_takes_page)."""
    esc = html.escape
    inp, row = (item.get("ledger") or {}).get("inputs", {}), item.get("row") or {}
    part = (read_toml(Path(item["dir"]) / "component.toml") or {}).get("part", {}) if item.get("dir") else {}
    url = inp.get("datasheet_url") or part.get("datasheet_url")
    main = Path(inp.get("datasheet_file") or part.get("datasheet_file") or "").name
    sheet = row.get("sheet") or main
    pdf_page = row.get("pdf_page")
    printed = row.get("printed_page")
    if not row:
        m = re.search(r"\bp\.\s*(\d+)", item.get("cite") or "")
        printed = f"{m.group(1)} (from the cite)" if m else None
    text = (f"local file <code>docs/datasheets/{esc(sheet) if sheet else 'n/a'}</code>, PDF p.{esc(str(pdf_page or 'n/a'))}, "
            f"printed p.{esc(str(printed or 'n/a'))}")
    if not url or (sheet and main and sheet != main):
        return text + ("; no URL for this sheet" if url else "; no URL")
    page = pdf_page or (re.match(r"\d+", str(printed)).group(0) if printed else None)
    if page and url_takes_page(url):
        return text + f'; <a href="{esc(url)}#page={esc(str(page))}">{esc(url)}#page={esc(str(page))}</a>'
    return text + f'; <a href="{esc(url)}">{esc(url)}</a> (no page anchor: not a direct PDF link)'


def collect(root, zl, name, out_tmp, python=sys.executable):
    """Run one block's analysis; (data dict, None) or (None, error text)."""
    script = Path(root) / "blocks" / name / "analysis.py"
    if not script.is_file():
        return None, f"blocks/{name}/analysis.py not found"
    dump = Path(out_tmp) / f"{name}.json"
    env = dict(os.environ, ZENER_LIBRARY=str(zl), PYTHONDONTWRITEBYTECODE="1")
    r = subprocess.run([python, "-c", DRIVER, str(script), str(Path(out_tmp) / name), str(dump)], cwd=root, env=env,
                       capture_output=True, text=True)
    if r.returncode or not dump.is_file():
        tail = (r.stderr or r.stdout).strip().splitlines()[-1:]
        return None, f"blocks/{name}/analysis.py exit {r.returncode}: {tail}"
    return json.loads(dump.read_text()), None


CSS = """
:root{--bg:#fff;--fg:#1b1b1b;--mute:#5d5d5d;--line:#d9d9d9;--card:#f6f6f4;--code:#efefec;--warn:#9a3b00;--ok:#1f6b3a}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#161616;--fg:#e6e6e6;--mute:#a3a3a3;
--line:#3a3a3a;--card:#1f1f1f;--code:#262626;--warn:#ffb27a;--ok:#7fd29b}}
body{background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,sans-serif;margin:0 auto;max-width:1000px;padding:16px}
h1{font-size:1.5em}h2{border-bottom:1px solid var(--line);padding-bottom:4px;margin-top:2em}
.item{background:var(--card);border:1px solid var(--line);border-radius:6px;margin:14px 0;padding:10px 14px}
.item h3{margin:0 0 6px;font-size:1.05em}.mute{color:var(--mute)}.warn{color:var(--warn)}
pre{background:var(--code);padding:8px;overflow-x:auto;font-size:13px;white-space:pre-wrap}
img{max-width:100%;display:block;margin:4px 0;background:#fff;border:1px solid var(--line)}
table{border-collapse:collapse}td,th{border:1px solid var(--line);padding:3px 8px;text-align:left}
"""


def stamp_text(item):
    """The reason line: what the row carries now and why it is listed."""
    stamp = item.get("stamp")
    if item["reason"] == "machine stamp" and stamp:
        return f"machine stamp (not a human check): {str(stamp).split()[0]}"
    return f"{item['reason']}: {stamp}" if stamp else item["reason"]


def render(name, zl, used, items, api):
    """One self-contained HTML page, no script: every <img> carries its PNG as a data URI (repeated where shared)."""
    esc = html.escape
    cache = {}

    def img(d, rel, alt):
        p = evidence(d, rel)
        if p is None:
            comp = "/".join(Path(d).parts[-3:])
            return (f'<p class="warn">no image ({esc(alt)}: {esc(rel)}): render locally with '
                    f'<code>python3 tools/ds_review.py {esc(comp)}</code> in the library (needs the PDF)</p>')
        if p not in cache:
            cache[p] = base64.b64encode(p.read_bytes()).decode()
        return f'<img src="data:image/png;base64,{cache[p]}" alt="{esc(alt)}">'

    counts = {t: sum(1 for i in items if i["tier"] == t) for t in TIERS}
    body = [f"<h1>{esc(name)}: human review packet</h1>",
            f'<p class="mute">Library: {esc(str(zl))}{"" if api else " (release without human-stamp support: every stamp listed)"}. '
            f"Rows the analysis reads: {len(used)}; without a current human stamp: {len(items)}.</p>",
            "<table><tr>" + "".join(f"<th>{esc(TITLES[t])}</th>" for t in TIERS) + "</tr><tr>"
            + "".join(f"<td>{counts[t]}</td>" for t in TIERS) + "</tr></table>",
            "<p>Check each row against its image: value, sign, unit, column (header image), conditions, footnotes, page. "
            "Never stamp from the text alone; existing machine stamps are not checks.</p>",
            f"<p><b>Several rows at once:</b> {esc(WHERE)} copy a tier's ref list below into a file, delete the rows you did "
            "not check, and run <code>python3 tools/stamp.py --who &lt;name&gt; --from FILE</code>.</p>"]
    for tier in TIERS:
        group = [i for i in items if i["tier"] == tier]
        if not group:
            continue
        body.append(f"<h2>{esc(TITLES[tier])} ({len(group)})</h2>")
        refs = [i["ref"] for i in group if i.get("ref") and tier in STAMPABLE]
        if refs:
            body.append(f"<p>Refs of this tier for <code>--from FILE</code> ({len(refs)}):</p><pre>" + esc("\n".join(refs)) + "</pre>")
        for i in group:
            parts = [f'<div class="item"><h3>{esc(i["input"])}</h3>',
                     f"<p>Reason: <b>{esc(stamp_text(i))}</b>; {esc(tier)}</p>",
                     f"<p>Page: {page_html(i)}</p>"]
            if i.get("why"):
                parts.append(f'<p class="mute">{esc(i["why"])}</p>')
            if i.get("hint"):
                parts.append('<p class="warn">check: value read from a graph / derived from \u00b1 (see note and cite)</p>')
            parts.append(f'<pre>{esc(i["toml"] or "cite: " + str(i.get("cite")))}</pre>')
            parts += [img(i["dir"], rel, alt) for rel, alt in i.get("images", [])]
            if tier == "page lookup":
                parts.append('<p class="warn">No usable image: open the page link and find the row yourself.</p>')
            if i.get("ref") and tier in STAMPABLE:
                parts.append(f"<p>{esc(WHERE)}</p><pre>python3 tools/stamp.py --who &lt;name&gt; {esc(i['ref'])}</pre>")
            parts.append("</div>")
            body.append("\n".join(parts))
    return ("<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{esc(name)} review packet</title><style>{CSS}</style></head><body>\n" + "\n".join(body)
            + "\n</body></html>\n")


def summary_lines(name, used, items):
    counts = {t: sum(1 for i in items if i["tier"] == t) for t in TIERS}
    reasons, parts = {}, {}
    for i in items:
        reasons[i["reason"]] = reasons.get(i["reason"], 0) + 1
        p = Path(i["dir"]).name if i.get("dir") else i["value"]
        parts[p] = parts.get(p, 0) + 1
    md = [f"### {name}", f"- rows read: {len(used)}; without a current human stamp: {len(items)}",
          "- by tier: " + ", ".join(f"{t} {counts[t]}" for t in TIERS),
          "- by reason: " + ", ".join(f"{k} {v}" for k, v in sorted(reasons.items())),
          "- by part: " + ", ".join(f"{k} {v}" for k, v in sorted(parts.items())), ""]
    warn = (f"::warning title=human review::{name}: {len(items)} of {len(used)} library rows read lack a current human "
            f"stamp (" + ", ".join(f"{t} {counts[t]}" for t in TIERS) + ")")
    return md, warn


def build(root, names, out_dir, zl):
    root, out_dir = Path(root), Path(out_dir)
    if not (zl / "lib" / "__init__.py").is_file():
        return 1, [f"error: no zener-library at {zl}"]
    found = blocks(root, names)
    if not found:
        return 1, ["error: no blocks/*/analysis.py"]
    out_dir.mkdir(parents=True, exist_ok=True)
    md, warns, rc = ["## Human review packet", f"Library: `{zl}`", ""], [], 0
    with tempfile.TemporaryDirectory(prefix="review_packet_") as tmp:
        for name in found:
            try:
                data, err = collect(root, zl, name, tmp)
                if err:
                    raise RuntimeError(err)
                items = [classify(dict(i)) for i in normalise(zl, data)]
                order = {t: n for n, t in enumerate(TIERS)}
                items.sort(key=lambda i: (order[i["tier"]], i["input"]))
                page = out_dir / f"{name}.html"
                page.write_text(render(name, zl, data["used"], items, data["api"]))
                lines, warn = summary_lines(name, data["used"], items)
            except Exception as e:  # one block's failure never hides the others
                rc = 1
                msg = " ".join(str(e).split())
                md += [f"### {name}", f"- ERROR: packet failed: {msg}", ""]
                warns.append(f"::warning title=human review::{name}: packet failed: {msg}")
                continue
            md += lines[:-1] + [f"- packet: `{page}` ({page.stat().st_size // 1024} KB)", ""]
            warns.append(warn)
    (out_dir / "summary.md").write_text("\n".join(md) + "\n")
    (out_dir / "annotations.txt").write_text("".join(w + "\n" for w in warns))
    return rc, md


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("blocks", nargs="*")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--library")
    ap.add_argument("--root", default=Path(__file__).resolve().parent.parent)
    a = ap.parse_args(argv)
    root = Path(a.root).resolve()
    rc, md = build(root, a.blocks, a.out_dir, zl_dir(root, a.library))
    print("\n".join(md))
    return rc


if __name__ == "__main__":
    sys.exit(main())
