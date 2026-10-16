"""Tests for cc/web.py against a real loopback server on a free port."""
import http.client
import json
import os
import tempfile
import threading
from pathlib import Path
import unittest
from pathlib import Path
from unittest import mock

from cc import store, web


class WebTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)   # the last request thread may still hold the db file on Windows
        self.dir = Path(self.tmp.name)
        self.srv = web.make_server(self.dir, 0)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.tmp.cleanup()

    def call(self, method, path, body=None, headers=None, host=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port)
        h = {"Host": host or f"127.0.0.1:{self.port}", **(headers or {})}
        raw = json.dumps(body).encode() if body is not None else None
        c.request(method, path, body=raw, headers=h)
        r = c.getresponse()
        data = r.read()
        c.close()
        try:
            return r.status, json.loads(data)
        except ValueError:
            return r.status, data

    def test_review_apply_endpoint(self):
        kit = self.dir / "kits" / "r1"
        kit.mkdir(parents=True)
        (kit / "tailor.json").write_text('{"h": "Azure Architect"}')
        conn = store.connect(self.dir / "commandcenter.db")
        store.set_doc(conn, "kitreviews", "r1", {"part_a": [{"file": "kit", "old_string": "Azure Architect",
                                                              "new_string": "Cloud Architect", "reason": "keyword"}]})
        conn.close()
        for bad in ({}, {"role": 5, "index": 0}, {"role": "r1", "index": "0"}, {"role": "r1", "index": 9},
                    {"role": "../x", "index": 0}):
            self.assertEqual(self.call("POST", "/review/apply", bad)[0], 400, bad)
        self.assertEqual(self.call("POST", "/review/apply", {"role": "r1", "index": 0},
                                   {"Origin": "http://evil.example"})[0], 403)
        self.assertEqual((kit / "tailor.json").read_text(), '{"h": "Azure Architect"}')
        code, r = self.call("POST", "/review/apply", {"role": "r1", "index": 0})
        self.assertEqual((code, r["applied"]), (200, [0]))
        self.assertIn("Cloud Architect", (kit / "tailor.json").read_text())
        self.assertEqual(self.call("POST", "/review/apply", {"role": "r1", "index": 0})[0], 400)

    def test_learned_endpoint(self):
        code, v = self.call("GET", "/learned")
        self.assertEqual((code, v["ai"], v["exclude"], v["suggested"]), (200, False, [], ["intern", "entry"]))
        for bad in ({}, {"action": "x"}, {"action": "forget", "term": 5}, {"action": "ai", "on": "yes"},
                    {"action": "exclude", "items": "a"}, {"action": "include", "items": [1]}):
            self.assertEqual(self.call("POST", "/learned", bad)[0], 400, bad)
        self.assertEqual(self.call("POST", "/learned", {"action": "ai", "on": True}, {"Origin": "http://evil.example"})[0], 403)
        self.assertTrue(self.call("POST", "/learned", {"action": "ai", "on": True})[1]["ai"])
        self.assertEqual(self.call("POST", "/learned", {"action": "exclude", "items": [" Oncology ", ""]})[1]["exclude"], ["oncology"])
        self.assertEqual(self.call("POST", "/learned", {"action": "levels", "items": ["entry", "senior"]})[1]["levels"], ["entry", "senior"])
        self.assertEqual(self.call("POST", "/learned", {"action": "levels", "items": ["wizard"]})[0], 400)
        self.assertEqual(self.call("POST", "/learned", {"action": "targets", "items": ["Marine Biologist"]})[1]["targets"], ["Marine Biologist"])
        self.assertEqual(self.call("POST", "/learned", {"action": "forget", "term": "oncology"})[1]["forgotten"], ["oncology"])

    def test_binds_loopback_only(self):
        self.assertEqual(self.srv.server_address[0], "127.0.0.1")

    def test_shim_keeps_query_string_out_of_path_encoding(self):
        # A percent-encoded "?since=" 404s on every poll and, with the lost-contact strip, flashed a red bar.
        shim = (Path(__file__).resolve().parent.parent / "web" / "shim.js").read_text(encoding="utf-8")
        self.assertIn("path.split('?')", shim)
        self.assertIn("conn(!e.network)", shim)

    def test_serves_page_and_shim(self):
        for p in ("/", "/shim.js", "/favicon.svg", "/favicon.ico"):
            code, body = self.call("GET", p)
            self.assertEqual(code, 200, p)
        self.assertIn(b"shim.js", self.call("GET", "/")[1])
        self.assertIn(b"<svg", self.call("GET", "/favicon.svg")[1])
        self.assertIn("first_name", self.call("GET", "/profile/status")[1])

    def test_source_credit_is_in_the_page(self):
        page = self.call("GET", "/")[1].decode()
        credit = ('<div class="src" id="source">Source: <a href="https://github.com/anoop900/command-center" '
                  'target="_blank" rel="noopener noreferrer">anoop900/command-center</a></div>')
        if credit not in page:      # the private tree shows a plain label; the export swaps in the credit
            credit = '<div class="src" id="source">Chief of Staff</div>'
        self.assertEqual(page.count(credit), 1)
        self.assertLess(page.index('id="nav-setup"'), page.index(credit))
        self.assertLess(page.index(credit), page.index("</nav>"))

    def test_demo_buttons_refresh_the_page_data_at_once(self):
        page = self.call("GET", "/")[1].decode()
        shim = self.call("GET", "/shim.js")[1].decode()
        self.assertIn("window.ccRefresh = ", shim)
        for step in ("toast('Demo loaded');", "toast('Demo data cleared');", "Demo data removed."):
            line = next(l for l in page.splitlines() if step in l)
            self.assertIn("ccRefresh", line, step)

    def test_logo_is_fixed_in_the_page(self):
        # In the sidebar and on the setup overlay, like the source credit: no setting or script path removes it.
        page = self.call("GET", "/")[1].decode()
        self.assertEqual(page.count('<svg class="logo'), 2)
        self.assertLess(page.index('<svg class="logo"'), page.index('id="nav-today"'))
        self.assertLess(page.index('id="wiz" hidden'), page.index('class="wizbrand"'))
        self.assertIn('<link rel="icon" type="image/svg+xml" href="/favicon.svg">', page)
        self.assertNotIn("wizbrand').remove", page)

    def test_wizard_state_reports_mode_and_ask_panel_warns_without_ai(self):
        code, body = self.call("GET", "/wizard")
        self.assertEqual(code, 200)
        self.assertTrue(body["ask_note"].startswith("No AI is set up."))   # a fresh data folder is in the default mode
        self.assertIn("askNotice();", self.call("GET", "/")[1].decode())

    def test_landing_rule_in_the_page(self):
        # No browser in the test run, so pin the rule: fresh -> Welcome, demo loaded -> Today, else profile if incomplete.
        page = self.call("GET", "/")[1].decode()
        self.assertIn("if (st.fresh) wizOpen()", page)
        self.assertIn("else if (!st.demo && pct < 100) wizProfile()", page)
        self.assertLess(page.index("if (st.fresh) wizOpen()"), page.index("else if (!st.demo && pct < 100)"))
        self.assertIn("You are looking at made-up demo data", page)

    def test_wizard_state_drives_the_landing(self):
        code, st = self.call("GET", "/wizard")
        self.assertEqual(code, 200)
        self.assertTrue(st["fresh"])
        self.assertFalse(st["demo"])
        self.assertEqual(self.call("POST", "/wizard/demo", {})[0], 200)
        self.assertEqual(self.call("POST", "/wizard/step", {"step": 0, "complete": True})[0], 200)   # what the page does next
        st = self.call("GET", "/wizard")[1]
        self.assertTrue(st["demo"])
        self.assertFalse(st["fresh"])

    def test_roundtrip_set_get_update_delete(self):
        code, r = self.call("PUT", "/api/tasks/t1", {"title": "a"}, {"If-Match": "*"})
        self.assertEqual((code, r["version"]), (200, 1))
        self.assertEqual(self.call("GET", "/api/tasks/t1")[1]["data"], {"title": "a"})
        code, r = self.call("PATCH", "/api/tasks/t1", {"done": True}, {"If-Match": "1"})
        self.assertEqual((code, r["version"]), (200, 2))
        code, r = self.call("GET", "/api/tasks")
        self.assertEqual([d["data"] for d in r["docs"]], [{"title": "a", "done": True}])
        self.assertEqual(self.call("DELETE", "/api/tasks/t1", headers={"If-Match": "2"})[0], 200)
        self.assertEqual(self.call("GET", "/api/tasks/t1")[0], 404)

    def test_writes_are_recorded_as_dashboard_with_history(self):
        self.call("PUT", "/api/tasks/t1", {"n": 1}, {"If-Match": "*"})
        self.call("PUT", "/api/tasks/t1", {"n": 2}, {"If-Match": "1"})
        conn = store.connect(self.dir / "commandcenter.db")
        self.assertEqual(store.get(conn, "tasks", "t1")["updated_by"], "dashboard")
        self.assertEqual(len(store.history(conn, "tasks", "t1")), 1)
        conn.close()

    def test_stale_version_is_409_and_missing_header_is_428(self):
        self.call("PUT", "/api/tasks/t1", {"n": 1}, {"If-Match": "*"})
        self.call("PUT", "/api/tasks/t1", {"n": 2}, {"If-Match": "1"})
        code, r = self.call("PUT", "/api/tasks/t1", {"n": 3}, {"If-Match": "1"})
        self.assertEqual((code, r["current"]), (409, 2))
        self.assertEqual(self.call("PUT", "/api/tasks/t1", {"n": 3})[0], 428)
        self.assertEqual(self.call("GET", "/api/tasks/t1")[1]["data"], {"n": 2})

    def test_snapshot_stamp_reports_unchanged_then_changed(self):
        self.call("PUT", "/api/tasks/t1", {"n": 1}, {"If-Match": "*"})
        stamp = self.call("GET", "/api/tasks")[1]["stamp"]
        self.assertTrue(self.call("GET", "/api/tasks?since=" + stamp.replace("|", "%7C"))[1]["unchanged"])
        self.call("PUT", "/api/tasks/t1", {"n": 2}, {"If-Match": "1"})
        self.assertIn("docs", self.call("GET", "/api/tasks?since=" + stamp.replace("|", "%7C"))[1])

    def test_rejects_foreign_host_origin_unknown_collection_and_bad_ids(self):
        self.assertEqual(self.call("GET", "/api/tasks", host="evil.example")[0], 403)
        self.assertEqual(self.call("PUT", "/api/tasks/t1", {"n": 1},
                                   {"If-Match": "*", "Origin": "http://evil.example"})[0], 403)
        self.assertEqual(self.call("GET", "/api/nope")[0], 404)
        self.assertEqual(self.call("PUT", "/api/tasks/..%2Fx", {"n": 1}, {"If-Match": "*"})[0], 404)
        self.assertEqual(self.call("PUT", "/api/tasks/t1", [1], {"If-Match": "*"})[0], 400)
        self.assertEqual(self.call("GET", "/api/tasks")[1]["docs"], [])

    def test_kit_files_served_without_traversal(self):
        (self.dir / "kits").mkdir()
        (self.dir / "kits" / "a.txt").write_text("hello", encoding="utf-8")
        (self.dir / "secret.txt").write_text("no", encoding="utf-8")
        code, body = self.call("GET", "/kits/a.txt")
        self.assertEqual((code, body), (200, b"hello"))
        for p in ("/kits/..%2Fsecret.txt", "/kits/../secret.txt", "/kits/missing.txt"):
            self.assertEqual(self.call("GET", p)[0], 404, p)

    def test_mode_endpoint_follows_config_and_reports_errors(self):
        env = {"CC_HOME": str(self.dir)}
        with mock.patch.dict(os.environ, env):
            self.assertEqual(self.call("GET", "/mode")[1]["mode"], "no-ai")
            (self.dir / "config.toml").write_text('[mode]\nname = "chat"\n', encoding="utf-8")
            code, body = self.call("GET", "/mode")
            self.assertEqual((code, body["label"], body["next"]["mode"]), (200, "Chat", "assisted"))
            (self.dir / "config.toml").write_text('[mode]\nname = "turbo"\n', encoding="utf-8")
            self.assertIn("expected one of", self.call("GET", "/mode")[1]["error"])
        self.assertEqual(self.call("GET", "/mode", host="evil.example")[0], 403)

    def test_mode_endpoint_warns_when_an_ai_mode_has_no_provider(self):
        env = {"CC_HOME": str(self.dir)}
        with mock.patch.dict(os.environ, env):
            for name, runner, want in (("assisted", "none", True), ("autopilot", "cli", False), ("chat", "none", False), ("no-ai", "none", False)):
                (self.dir / "config.toml").write_text(
                    f'[mode]\nname = "{name}"\n[ai]\nrunner = "{runner}"\n', encoding="utf-8")
                self.assertIs(self.call("GET", "/mode")[1]["ai_missing"], want, (name, runner))

    def test_oversize_body_refused(self):
        c = http.client.HTTPConnection("127.0.0.1", self.port)
        c.putrequest("PUT", "/api/tasks/t1")
        c.putheader("Content-Length", str(web.MAX_BODY + 1))
        c.putheader("If-Match", "*")
        c.endheaders()
        self.assertEqual(c.getresponse().status, 413)
        c.close()


if __name__ == "__main__":
    unittest.main()
