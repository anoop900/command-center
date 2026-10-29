"""Routes the browser extension uses: read-only data from the dashboard, and one guarded write."""
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from cc import store, web


class ExtensionApi(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.srv = web.make_server(self.dir, 0)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        k = self.dir / "kits" / "2026-10-29" / "Contoso_Head_of_Ops"
        k.mkdir(parents=True)
        (k / "Jordan_Example_Resume.pdf").write_bytes(b"%PDF-1.4 test")
        c = store.connect(self.dir / "commandcenter.db")
        for rid, status, kit in (("contoso-head-of-ops", "ready", "2026-10-29/Contoso_Head_of_Ops"),
                                 ("old-co-x", "rejected", "2026-10-29/Contoso_Head_of_Ops"),
                                 ("nokit-co-y", "ready", ""), ("evil-co-z", "ready", "../../..")):
            store.set_doc(c, "roles", rid, {"key": rid, "company": rid.split("-")[0].title(), "title": "Head of Ops",
                                            "status": status, "kit": kit, "link": "https://jobs.lever.co/contoso/12345678"})
        c.close()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.tmp.cleanup()

    def call(self, method, path, body=None, headers=None, raw=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port)
        h = {"Host": f"127.0.0.1:{self.port}", **(headers or {})}
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        c.request(method, path, body=data, headers=h)
        r = c.getresponse()
        out = r.read()
        c.close()
        return r.status, out, r

    def js(self, *a, **k):
        code, out, _ = self.call(*a, **k)
        return code, json.loads(out)

    def test_roles_lists_only_open_roles_with_a_resume_and_a_safe_folder(self):
        code, out = self.js("GET", "/extension/roles")
        self.assertEqual(code, 200)
        self.assertEqual([r["key"] for r in out["roles"]], ["contoso-head-of-ops"])
        self.assertEqual(out["roles"][0]["job_id"], "12345678")

    def test_resume_bytes_and_bad_keys(self):
        code, out, r = self.call("GET", "/extension/resume/contoso-head-of-ops")
        self.assertEqual((code, out, r.getheader("X-Resume-Name")), (200, b"%PDF-1.4 test", "Jordan_Example_Resume.pdf"))
        for key in ("nokit-co-y", "evil-co-z", "missing", "..%2f..%2fetc", "x" * 300, "%00"):
            self.assertEqual(self.call("GET", "/extension/resume/" + key)[0], 404, key)

    def test_profile_reports_what_is_missing(self):
        code, out = self.js("GET", "/extension/profile")
        self.assertEqual(code, 200)
        self.assertEqual(out["profile"], {})
        self.assertIn("firstName", [m["key"] for m in out["missing"]])
        self.assertEqual(self.js("GET", "/extension/health")[1]["profile"], False)

    def test_wrong_host_refused(self):
        self.assertEqual(self.call("GET", "/extension/roles", headers={"Host": "evil.example"})[0], 403)

    def test_mark_applied_needs_the_extension_origin_and_header(self):
        ext = {"Origin": "chrome-extension://abcdef", "X-CC-Extension": "1", "Content-Type": "application/json"}
        body = {"key": "contoso-head-of-ops"}
        self.assertEqual(self.call("POST", "/extension/applied", body, {**ext, "X-CC-Extension": "0"})[0], 403)
        self.assertEqual(self.call("POST", "/extension/applied", body, {**ext, "Origin": "https://evil.example"})[0], 403)
        self.assertEqual(self.call("POST", "/extension/applied", body, {"Origin": "chrome-extension://abc"})[0], 403)
        # the extension Origin is accepted for this route only
        self.assertEqual(self.call("POST", "/add-role", {"company": "A", "title": "B"}, ext)[0], 403)
        code, out = self.js("POST", "/extension/applied", body, ext)
        self.assertEqual((code, out["already"]), (200, False))
        c = store.connect(self.dir / "commandcenter.db")
        self.assertTrue(store.get(c, "roles", "contoso-head-of-ops")["data"]["status"].startswith("applied"))
        c.close()
        self.assertTrue(self.js("POST", "/extension/applied", body, ext)[1]["already"])    # repeat is a no-op
        self.assertEqual([r["key"] for r in self.js("GET", "/extension/roles")[1]["roles"]], ["contoso-head-of-ops"])

    def test_mark_applied_hostile_bodies(self):
        ext = {"Origin": "chrome-extension://abcdef", "X-CC-Extension": "1"}
        for raw in (b"", b"null", b"[1]", b"{", b'{"key": 5}', b'{"key": ""}', b'{"key": "nope"}', b'{"key": "' + b"x" * 5000 + b'"}'):
            code, _, _ = self.call("POST", "/extension/applied", headers=ext, raw=raw)
            self.assertIn(code, (400, 404, 413), raw[:20])

    def test_unknown_extension_path(self):
        self.assertEqual(self.call("GET", "/extension/nope")[0], 404)


if __name__ == "__main__":
    unittest.main()
