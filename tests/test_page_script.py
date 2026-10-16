"""The dashboard's inline script must parse; a syntax error silently kills every screen."""
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

PAGE = Path(__file__).resolve().parent.parent / "web" / "index.html"


@unittest.skipUnless(shutil.which("node"), "node not installed")
class PageScriptParses(unittest.TestCase):
    def test_inline_scripts_parse(self):
        html = PAGE.read_text(encoding="utf-8")
        js = "\n".join(re.findall(r"<script>(.*?)</script>", html, re.S))
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "page.js"
            f.write_text(js, encoding="utf-8")
            r = subprocess.run(["node", "--check", str(f)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr[:400])


@unittest.skipUnless(shutil.which("node"), "node not installed")
class OfferNumberParsing(unittest.TestCase):
    def test_num_reads_money_strictly(self):
        html = PAGE.read_text(encoding="utf-8")
        fn = re.search(r"^const num = (.*);$", html, re.M).group(1)
        cases = {"1e999": None, "abc": None, "12-14": None, "": None, "120k": 120000, "$120,000": 120000,
                 "CAD 95,500.50": 95500.5, "120000": 120000}
        js = f"const num = {fn}; console.log(JSON.stringify({list(cases)!r}.map(num)))"
        r = subprocess.run(["node", "-e", js], capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), str(list(cases.values())).replace("None", "null").replace(" ", ""), r.stderr[:300])


@unittest.skipUnless(shutil.which("node"), "node not installed")
class WizardResultBanner(unittest.TestCase):
    def test_pass_fail_and_working_are_told_apart(self):
        html = PAGE.read_text(encoding="utf-8")
        fn = re.search(r"^function wizMsg\(.*?^}$", html, re.M | re.S).group(0)
        js = """
const els = {};
const mk = () => ({style: {}, attrs: {}, hidden: true, setAttribute(k, v) { this.attrs[k] = v; }});
const $ = id => id === 'wiz-banner' ? els.b : (els[id] = els[id] || {textContent: ''});
global.document = {createElement: () => (els.b = mk()), body: {appendChild() {}}};
const esc = s => String(s).replace(/</g, '&lt;');
let timers = 0; global.setTimeout = () => { timers++; return 1; }; global.clearTimeout = () => {};
""" + fn + """
const out = [];
for (const [ok, t] of [[false, 'Mail test failed: bad <b>'], [true, 'The mail server accepted the login.'], [true, 'Testing...']]) {
  const before = timers; wizMsg(ok, t);
  out.push({role: els.b.attrs.role, failed: els.b.innerHTML.includes('Failed.'), done: els.b.innerHTML.includes('Done.'),
            hidden: els.b.hidden, autoHides: timers > before, escaped: !els.b.innerHTML.includes('<b>bad')});
}
console.log(JSON.stringify(out));
"""
        r = subprocess.run(["node", "-e", js], capture_output=True, text=True)
        import json
        fail, ok, busy = json.loads(r.stdout)
        self.assertEqual((fail["role"], fail["failed"], fail["done"], fail["autoHides"], fail["hidden"]), ("alert", True, False, False, False), r.stderr[:300])
        self.assertTrue(fail["escaped"])
        self.assertEqual((ok["role"], ok["done"], ok["failed"], ok["autoHides"]), ("status", True, False, True))
        self.assertEqual((busy["done"], busy["failed"], busy["autoHides"]), (False, False, False))


if __name__ == "__main__":
    unittest.main()
