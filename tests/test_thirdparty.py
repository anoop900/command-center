"""Import from other tools (CSV/XLSX trackers, JSON Resume): mapping, preview-writes-nothing, idempotence, hostile files,
and the HTTP/CLI paths. All fixtures are synthetic."""
import http.client
import io
import json
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from cc import cli, drafts, store, thirdparty, web

CSV = ("Company,Position,Status,URL,Date Applied,Notes,Salary,Extra\n"
       "Acme Corp,Cloud Architect,Applied,https://example.com/jobs/123456789,2026-09-01,Phone screen booked,150000,x\n"
       "Globex,Platform Lead,Interview,javascript:alert(1),09/15/2026,=HYPERLINK(\"http://x\"),,y\n"
       ",No Company,Applied,,,,,\n"
       "Acme Inc.,Cloud Architect,Applied,,,,,\n")
RESUME = {"basics": {"name": "Jordan Q Example", "email": "jordan@example.com",
                     "location": {"city": "Springfield", "countryCode": "US"},
                     "profiles": [{"network": "LinkedIn", "url": "https://linkedin.com/in/jordan-example"}]},
          "work": [{"name": "Initech", "position": "Engineer", "startDate": "2020-01", "highlights": ["Cut costs 10%"]}],
          "skills": [{"name": "Cloud", "keywords": ["Azure", "Terraform"]}]}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name) / "data"
        self.d.mkdir()
        self.conn = store.connect(self.d / "commandcenter.db")
        self.addCleanup(lambda: self.conn.close())

    def file(self, name, content):
        p = Path(self.tmp.name) / name
        p.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
        return p

    def roles(self):
        return store.list_docs(self.conn, "roles")


class Tracker(Base):
    def test_preview_writes_nothing_and_maps_headers(self):
        pv = thirdparty.preview(self.conn, self.d, self.file("t.csv", CSV))
        self.assertEqual((pv["roles"], pv["duplicates"], pv["skipped_count"]), (2, 1, 1))
        self.assertEqual(pv["mapped"]["title"], "Position")
        self.assertIn("Salary", pv["unmapped_headers"])
        self.assertEqual(self.roles(), [])

    def test_apply_stores_imported_roles_text_only_and_is_idempotent(self):
        p = self.file("t.csv", CSV)
        self.assertEqual(thirdparty.apply(self.conn, self.d, p)["roles"], 2)
        self.assertEqual(thirdparty.apply(self.conn, self.d, p)["roles"], 0)
        by = {r["data"]["company"]: r["data"] for r in self.roles()}
        self.assertEqual(len(by), 2)
        a, g = by["Acme Corp"], by["Globex"]
        self.assertEqual((a["status"], a["source_status"], a["applied_date"], a["pay"]), ("imported", "Applied", "2026-09-01", ""))
        self.assertEqual(g["link"], "")  # javascript: is not a link
        self.assertEqual(g["applied_date"], "2026-09-15")
        self.assertTrue(g["reason_or_gaps"].startswith("="))  # kept as inert text

    def test_existing_roles_are_not_duplicated(self):
        store.set_doc(self.conn, "roles", "x", {"key": "acme|cloud architect", "company": "Acme", "title": "Cloud Architect", "link": ""})
        self.assertEqual(thirdparty.apply(self.conn, self.d, self.file("t.csv", CSV))["roles"], 1)

    def test_encodings_and_delimiters(self):
        body = "Company;Job Title\nEcole Co;Prof\n"
        for raw in (b"\xef\xbb\xbf" + body.encode(), body.encode("utf-16"), body.encode("cp1252")):
            pv = thirdparty.preview(self.conn, self.d, self.file("t.csv", raw))
            self.assertEqual(pv["roles"], 1, raw[:6])

    def test_xlsx(self):
        import openpyxl
        wb = openpyxl.Workbook()
        wb.active.append(["Employer", "Role", "Stage", "Applied"])
        wb.active.append(["Initech", "SRE", "Applied", None])
        p = Path(self.tmp.name) / "t.xlsx"
        wb.save(p)
        self.assertEqual(thirdparty.apply(self.conn, self.d, p)["roles"], 1)


class Hostile(Base):
    def bad(self, name, content):
        with self.assertRaises(thirdparty.ImportError_):
            thirdparty.preview(self.conn, self.d, self.file(name, content))
        self.assertEqual(self.roles(), [])

    def test_refused_files(self):
        self.bad("t.csv", b"")
        self.bad("t.csv", b"a,b\x00c\n1,2\n")
        self.bad("t.csv", "Name,Age\nx,1\n")
        self.bad("t.csv", "Company,Position\n" + "a,b\n" * 6000)
        self.bad("t.csv", b"x" * (thirdparty.MAX_BYTES + 1))
        self.bad("t.json", "{" * 100000)
        self.bad("t.json", "[" * 100000)
        self.bad("t.json", "[1,2]")
        self.bad("t.xlsx", b"not a zip")
        self.bad("t.pdf", b"%PDF-1.4 binary")

    def test_zip_bomb_xlsx_refused_before_parsing(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("xl/big.xml", b"0" * (thirdparty.MAX_UNPACKED + 1))
        self.bad("bomb.xlsx", buf.getvalue())

    def test_huge_cells_and_control_chars_are_cut(self):
        body = "Company,Position,Notes\n" + "A" * 9000 + ",T\x07x," + "n" * 9000 + "\n"
        out = thirdparty.apply(self.conn, self.d, self.file("t.csv", body))
        r = self.roles()[0]["data"]
        self.assertEqual((out["roles"], len(r["company"]), len(r["reason_or_gaps"])), (1, 200, 1000))
        self.assertNotIn("\x07", r["title"])

    def test_resume_wrong_types_do_not_crash(self):
        docs = ({"basics": 5, "work": "x", "skills": 7}, {"basics": {"name": 5, "email": ["a"], "profiles": [5, None]}},
                {"work": [None, 3, {"name": 4, "position": []}], "skills": [None, {"keywords": "abc"}]}, {})
        for doc in docs:
            thirdparty.preview(self.conn, self.d, self.file("r.json", json.dumps(doc)))


class Resume(Base):
    def test_draft_is_unconfirmed_and_keeps_typed_values(self):
        typed = {"fields": {"firstName": {"value": "Typed", "ok": False}}, "jobs": [], "skills": {"value": "", "ok": False}}
        drafts.write(self.d, "profile", typed)
        p = self.file("r.json", json.dumps(RESUME))
        pv = thirdparty.preview(self.conn, self.d, p)
        self.assertEqual(drafts.read(self.d, "profile")["fields"].get("lastName"), None)  # preview wrote nothing
        self.assertNotIn("firstName", pv["draft_adds"])
        thirdparty.apply(self.conn, self.d, p)
        st = drafts.read(self.d, "profile")
        self.assertEqual(st["fields"]["firstName"]["value"], "Typed")
        self.assertEqual(st["fields"]["lastName"]["value"], "Q Example")
        self.assertEqual(st["fields"]["linkedin"]["value"], "https://linkedin.com/in/jordan-example")
        self.assertEqual(st["skills"], {"value": "Azure, Terraform", "ok": False})
        self.assertFalse(any(c["ok"] for c in st["fields"].values()) or any(j["ok"] for j in st["jobs"]))
        self.assertEqual(st["jobs"][0]["employer"], "Initech")


class Interfaces(Base):
    def setUp(self):
        super().setUp()
        self.conn.close()
        self.srv = web.make_server(self.d, 0)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (self.srv.shutdown(), self.srv.server_close()))
        self.conn = store.connect(self.d / "commandcenter.db")

    def post(self, path, body, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        c.request("POST", path, body, headers or {})
        r = c.getresponse()
        out = r.read()
        c.close()
        return r.status, json.loads(out)

    def test_http_preview_then_apply(self):
        st, j = self.post("/import-other/preview?name=t.csv", CSV.encode())
        self.assertEqual((st, j["roles"]), (200, 2))
        self.assertEqual(self.roles(), [])
        st, j = self.post("/import-other/apply?name=t.csv", CSV.encode())
        self.assertEqual((st, j["roles"]), (200, 2))
        self.assertEqual(len(self.roles()), 2)

    def test_http_bad_requests(self):
        cases = (("/import-other/preview", CSV.encode()), ("/import-other/preview?name=t.exe", b"x"),
                 ("/import-other/preview?name=../../t.csv", b""), ("/import-other/apply?name=t.csv", b"\x00\x01"),
                 ("/import-other/apply?name=t.json", b"{" * 5000))
        for path, body in cases:
            st, j = self.post(path, body)
            self.assertEqual(st, 400, path)
            self.assertIn("error", j)
        st, _ = self.post("/import-other/preview?name=t.csv", b"x", {"Host": "evil.example"})
        self.assertEqual(st, 403)
        self.assertEqual(self.roles(), [])

    def test_cli_preview_yes_and_refusal(self):
        p = str(self.file("t.csv", CSV))
        with mock.patch("cc.cli.api.db_path", return_value=self.d / "commandcenter.db"):
            self.assertEqual(cli._import_other(self.conn, [p, "--preview"]), 0)
            self.assertEqual(self.roles(), [])
            with mock.patch("builtins.input", side_effect=EOFError):
                self.assertEqual(cli._import_other(self.conn, [p]), 1)
            self.assertEqual(self.roles(), [])
            self.assertEqual(cli._import_other(self.conn, [p, "--yes"]), 0)
            self.assertEqual(len(self.roles()), 2)
            self.assertEqual(cli._import_other(self.conn, [str(self.d / "missing.csv"), "--yes"]), 1)
            self.assertEqual(cli._import_other(self.conn, []), 2)


if __name__ == "__main__":
    unittest.main()
