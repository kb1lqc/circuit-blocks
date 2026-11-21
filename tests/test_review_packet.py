"""Tests for tools/review_packet.py with a fake library and fake block analyses (no real library needed)."""

import base64
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import review_packet as rp  # noqa: E402

COMPONENT = '''[part]
value = "X"
datasheet_url = "https://example.com/x.pdf"

[param.a]
min = 1
unit = "V"
cite = "DS p.2, T, a"
verified = "sonnet-audit 2026-11-18 blind"

[param.b]
max = 2
unit = "V"
cite = "DS p.2, T, b"

[param.c]
max = 3
unit = "V"
cite = "DS p.3, T, c"
verified = "human:bob 2026-11-20 page-image ds_review abc"

[param.d]
max = 4
unit = "V"
cite = "DS p.4, T, d"

[param.e]
max = 5
unit = "V"
cite = "DS p.5, T, e"
# section comment that belongs to the next table

[param.f]
max = 6
unit = "V"
cite = "DS p.6, T, f"

[param.g]
max = 7
unit = "V"
cite = "DS p.7, T, g"
note = "read from the typical curve"

[param.h]
max = 8
unit = "V"
cite = "DS p.8, T, h"
'''

# API library: rows needing review computed from the TOML (stamp prefix human: = human), as the real lib does.
LIB_API = '''import tomllib
from pathlib import Path
D = Path(__file__).resolve().parent.parent / "components" / "Acme" / "X"
_USED = []
def read(key):
    _USED.append(key)
def used_inputs():
    return sorted(f"X:{k}" for k in set(_USED))
def unverified_inputs():
    t = tomllib.loads((D / "component.toml").read_text())["param"]
    return sorted(f"X:{k}" for k in set(_USED) if not t[k].get("verified"))
def human_review_needed():
    t = tomllib.loads((D / "component.toml").read_text())["param"]
    out = []
    for k in sorted(set(_USED)):
        s = t[k].get("verified")
        if s and s.startswith("human:"):
            continue
        out.append({"input": f"X:{k}", "value": "X", "key": k, "dir": str(D), "review_key": k, "ref": f"X:{k}",
                    "stamp": s, "state": None, "cite": t[k]["cite"], "reason": "machine stamp" if s else "no stamp"})
    return out
'''
LIB_OLD = LIB_API.split("def human_review_needed")[0]

ANALYSIS = '''import os, sys
from pathlib import Path
sys.path.insert(0, os.environ["ZENER_LIBRARY"])
import lib
i = sys.argv.index("--out")
Path(sys.argv[i + 1]).mkdir(parents=True, exist_ok=True)
(Path(sys.argv[i + 1]) / "design-analysis.md").write_text("report")
for k in KEYS:
    lib.read(k)
'''


def write(p, data):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data) if isinstance(data, bytes) else p.write_text(data)


def ledger_row(key, table, **kw):
    kw.setdefault("hash", rp.row_hash(table))
    return f"[row.{key}]\n" + "".join(f"{k} = {json.dumps(v)}\n" for k, v in kw.items())


def png(height, tag=b""):
    """A PNG signature + IHDR with the given pixel height (enough for png_height), plus a tag to tell files apart."""
    return b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + (100).to_bytes(4, "big") + height.to_bytes(4, "big") + tag


KEYS = ["a", "b", "c", "d", "e", "f", "g", "h"]


class Packet(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        t = Path(self._t.name)
        self.root, self.zl, self.out = t / "cb", t / "zl", t / "out"
        write(self.root / "blocks/B/analysis.py", ANALYSIS.replace("KEYS", repr(KEYS)))
        write(self.root / "blocks/C/analysis.py", ANALYSIS.replace("KEYS", repr(["a"])))
        write(self.root / "benches/B/docs/design-analysis.md", "committed")
        write(self.zl / "lib/__init__.py", LIB_API)
        self.comp = self.zl / "components/Acme/X"
        write(self.comp / "component.toml", COMPONENT)

    def tearDown(self):
        self._t.cleanup()

    def review(self, mutate=None, url="https://example.com/x.pdf"):
        import tomllib
        params = tomllib.loads(COMPONENT)["param"]
        if mutate:
            params[mutate] = {**params[mutate], "max": 99}
        fb = dict(state="NEW", status="UNSURE", crop="fallback", dpi=150)
        rows = [ledger_row("a", params["a"], state="VERIFIED", status="AGREE_TEXT_CHECK", crop="auto", pdf_page=2,
                           printed_page=2, image="evidence/a_p2.png", header_image="evidence/head_p2.png"),
                ledger_row("b", params["b"], state="NEW", status="UNSURE", crop="auto", pdf_page=2, printed_page=2,
                           image="evidence/b_p2.png", header_image="evidence/head_p2.png"),
                ledger_row("d", params["d"], pdf_page=4, printed_page=4, image="evidence/table_p4_y90.png", **fb),
                ledger_row("e", params["e"], state="NEW", status="DISAGREE", reason="max differs", crop="auto",
                           pdf_page=5, printed_page=5, image="evidence/e_p5.png"),
                ledger_row("g", params["g"], pdf_page=7, printed_page=7, image="evidence/table_p7_y73.png", **fb),
                ledger_row("h", params["h"], pdf_page=8, printed_page=8, image="evidence/table_p8_y73.png", **fb)]
        write(self.comp / "review/review.toml", f'[inputs]\ndatasheet_url = "{url}"\n'
              'datasheet_file = "docs/datasheets/x.pdf"\n' + "\n".join(rows))
        for name, h in (("a_p2", 40), ("b_p2", 40), ("head_p2", 60), ("table_p4_y90", 400), ("e_p5", 40),
                        ("table_p7_y73", 60), ("table_p8_y73", 60), ("page_p8", 1600)):
            write(self.comp / f"review/evidence/{name}.png", png(h, name.encode()))

    def build(self, *names):
        return rp.build(self.root, list(names), self.out, self.zl.resolve())

    def items(self, page):
        return re.findall(r"<h2>(.*?) \((\d+)\)</h2>", page)

    def block(self, page, ref):
        return re.search(rf"<h3>{re.escape(ref)}</h3>.*?</div>", page, re.S).group(0)

    def test_order_tiers_images_and_stamp_commands(self):
        self.review()
        rc, md = self.build("B")
        self.assertEqual(rc, 0, md)
        page = (self.out / "B.html").read_text()
        self.assertEqual(self.items(page), [(rp.TITLES["fix"], "1"), (rp.TITLES["tier 1"], "1"),
                                            (rp.TITLES["tier 2"], "1"), (rp.TITLES["tier 3"], "2"),
                                            (rp.TITLES["page lookup"], "1"), (rp.TITLES["no record"], "1")])
        self.assertLess(page.index("X:e</h3>"), page.index("X:a</h3>"))  # fix first
        self.assertNotIn("X:c</h3>", page)  # current human stamp (per the lib): not listed
        for ref in ("X:a", "X:b", "X:d", "X:h", "X:g"):  # tiers 1-3 and page lookup carry a stamp command
            self.assertIn(f"python3 tools/stamp.py --who &lt;name&gt; {ref}</pre>", self.block(page, ref))
        for ref in ("X:e", "X:f"):  # fix and no record: none
            self.assertNotIn("tools/stamp.py --who", self.block(page, ref))
        self.assertIn("zener-library working copy", self.block(page, "X:a"))
        self.assertIn("--from FILE", page[:page.index("<h2>")])  # the several-rows line is at the top
        self.assertIn("<pre>X:a</pre>", page)  # tier 1 ref list
        self.assertNotIn("<script", page)
        self.assertEqual(page.count(base64.b64encode(png(60, b"head_p2")).decode()), 2)  # shared header repeated, no JS
        self.assertIsNone(re.search(r"^verified", page, re.M))  # machine stamp not shown as part of the TOML
        self.assertIn("machine stamp (not a human check): sonnet-audit", self.block(page, "X:a"))
        self.assertNotIn("section comment", page)
        a = self.block(page, "X:a")
        self.assertIn("docs/datasheets/x.pdf</code>, PDF p.2, printed p.2", a)
        self.assertIn('href="https://example.com/x.pdf#page=2"', a)
        self.assertIn("printed p.6 (from the cite)", self.block(page, "X:f"))
        self.assertIn("PDF p.n/a", self.block(page, "X:f"))
        self.assertIn("check: value read from a graph", self.block(page, "X:g"))
        self.assertNotIn("check: value read from a graph", a)
        summary = (self.out / "summary.md").read_text()
        self.assertIn("rows read: 8; without a current human stamp: 7", summary)
        ann = (self.out / "annotations.txt").read_text().splitlines()
        self.assertEqual(ann, ["::warning title=human review::B: 7 of 8 library rows read lack a current human stamp "
                               "(fix 1, tier 1 1, tier 2 1, tier 3 2, page lookup 1, no record 1)"])
        self.assertEqual((self.root / "benches/B/docs/design-analysis.md").read_text(), "committed")

    def test_short_fallback_crop(self):
        self.review()
        self.build("B")
        page = (self.out / "B.html").read_text()
        g, h, d = self.block(page, "X:g"), self.block(page, "X:h"), self.block(page, "X:d")
        self.assertIn("page lookup", g)
        self.assertNotIn("<img", g)  # 60 px at 150 dpi = 28.8 pt < MIN_FALLBACK_PT: not shown
        self.assertIn("too short", g)
        self.assertIn('alt="full page"', h)  # replaced by the page image
        self.assertNotIn('alt="table or page"', h)
        self.assertIn('alt="table or page"', d)  # 400 px: kept
        self.assertEqual(rp.png_height(self.comp / "review/evidence/page_p8.png"), 1600)

    def test_cache_preferred_then_old_review_then_no_image(self):
        self.review()
        cache = self.zl / ".review-cache/Acme/X/evidence"
        cache.mkdir(parents=True)
        (cache / "a_p2.png").write_bytes(png(40, b"from-cache"))
        self.build("B")
        a = self.block((self.out / "B.html").read_text(), "X:a")
        self.assertIn(base64.b64encode(png(40, b"from-cache")).decode(), a)  # the library's local cache wins
        import shutil
        shutil.rmtree(self.zl / ".review-cache")
        shutil.rmtree(self.comp / "review/evidence")  # current release in CI: no images anywhere
        rc, md = self.build("B")
        self.assertEqual(rc, 0, md)
        page = (self.out / "B.html").read_text()
        self.assertNotIn("<img", page)
        self.assertIn("no image (row: evidence/a_p2.png): render locally", self.block(page, "X:a"))
        self.assertIn("tools/ds_review.py components/Acme/X", self.block(page, "X:a"))
        self.assertIn("tier 3", self.block(page, "X:d"))  # absent fallback image: height unknown, not demoted

    def test_page_anchor_rule(self):
        self.assertTrue(rp.url_takes_page("https://a.com/x.PDF"))
        self.assertTrue(rp.url_takes_page("https://www.ti.com/lit/ds/symlink/ina229"))
        self.assertTrue(rp.url_takes_page("https://x.com/doc?id=1"))
        self.assertFalse(rp.url_takes_page("https://www.infineon.com"))
        self.review(url="https://www.infineon.com")
        self.build("B")
        a = self.block((self.out / "B.html").read_text(), "X:a")
        self.assertNotIn("#page=", a)
        self.assertIn('href="https://www.infineon.com"', a)

    def test_record_older_than_row_goes_to_fix(self):
        self.review(mutate="b")
        self.build("B")
        page = (self.out / "B.html").read_text()
        self.assertIn("review record older than the committed row", self.block(page, "X:b"))
        self.assertNotIn("tools/stamp.py --who", self.block(page, "X:b"))

    def test_old_library_degrades(self):
        write(self.zl / "lib/__init__.py", LIB_OLD)  # no human_review_needed, no review/ dirs
        rc, md = self.build()
        self.assertEqual(rc, 0, md)
        page = (self.out / "B.html").read_text()
        self.assertEqual(self.items(page), [(rp.TITLES["no record"], "8")])
        self.assertIn("release without human-stamp support", page)
        self.assertIn("no review record: human:bob", self.block(page, "X:c"))  # cannot be checked: listed
        self.assertIn("cite = &quot;DS p.4, T, d&quot;", page)
        self.assertIn("https://example.com/x.pdf#page=4", page)
        self.assertNotIn("tools/stamp.py --who", page.split("<h2>")[-1])
        self.assertTrue((self.out / "C.html").is_file())
        self.assertEqual(len((self.out / "annotations.txt").read_text().splitlines()), 2)

    def test_failing_block_warns_others_built(self):
        write(self.root / "blocks/C/analysis.py", "raise SystemExit(3)")
        rc, md = self.build()
        self.assertEqual(rc, 1)
        self.assertTrue(any("packet failed" in ln and "exit 3" in ln for ln in md), md)
        self.assertTrue((self.out / "B.html").is_file())
        ann = (self.out / "annotations.txt").read_text().splitlines()
        self.assertTrue(ann[0].startswith("::warning title=human review::B: "))
        self.assertTrue(ann[1].startswith("::warning title=human review::C: packet failed: "), ann)
        self.assertTrue((self.out / "summary.md").is_file())
        self.assertEqual(self.build("Nope")[0], 1)

    def test_render_crash_is_caught(self):
        self.review()
        orig = rp.render
        rp.render = lambda *a: 1 / 0
        try:
            rc, md = self.build("B")
        finally:
            rp.render = orig
        self.assertEqual(rc, 1)
        self.assertIn("packet failed: division by zero", (self.out / "annotations.txt").read_text())

    def test_no_library_or_blocks(self):
        self.assertEqual(rp.build(self.root, [], self.out, self.zl / "missing")[0], 1)
        self.assertEqual(rp.build(self.root / "empty", [], self.out, self.zl)[0], 1)


if __name__ == "__main__":
    unittest.main()
