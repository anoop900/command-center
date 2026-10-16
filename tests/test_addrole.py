"""Add a role by hand (POST /add-role): lands on the board, no duplicates, bad input gets a clean 400."""
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from cc import store, web


class AddRole(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.srv = web.make_server(self.dir, 0)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.tmp.cleanup()

    def post(self, body, raw=None, host=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port)
        c.request("POST", "/add-role", body=raw if raw is not None else json.dumps(body).encode(),
                  headers={"Host": host or f"127.0.0.1:{self.port}", "Content-Type": "application/json"})
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, json.loads(data)

    def conn(self):
        return store.connect(self.dir / "commandcenter.db")

    def test_added_role_reaches_the_board_even_before_any_run(self):
        code, out = self.post({"company": "Contoso", "title": "Head of Ops", "posting": "Run operations.", "build": False})
        self.assertEqual((code, out["created"]), (200, True))
        c = self.conn()
        role = store.get(c, "roles", out["id"])["data"]
        board = store.get(c, "board", "roles")["data"]["roles"]
        c.close()
        self.assertEqual((role["source"], role["posting"]), ("manual", "Run operations."))
        self.assertEqual([(r["company"], r["stage"], r["source"]) for r in board], [("Contoso", "new", "manual")])

    def test_a_role_you_added_never_ages_off_the_board_but_a_feed_role_does(self):
        import datetime as dt
        from cc.runs import board
        old = {"company": "A", "title": "T", "status": "build on request", "decision": "queued", "date_found": "2026-01-01"}
        rows = [dict(old, source="manual", key="a"), dict(old, source="feed", key="b")]
        got = board.build_roles(rows, {}, dt.date(2026, 6, 1))
        self.assertEqual([r["key"] for r in got], ["a"])

    def test_link_only_on_unknown_site_asks_for_the_description(self):
        code, out = self.post({"company": "Contoso", "title": "Head of Ops", "link": "https://example.org/job/1"})
        self.assertEqual(code, 200)
        self.assertEqual(out["job"]["state"], "needs_posting")

    def test_same_role_twice_is_one_role_and_one_build(self):
        a = self.post({"company": "Contoso", "title": "Head of Ops", "posting": "x", "build": False})[1]
        code, b = self.post({"company": "Contoso", "title": "Head of Ops", "posting": "x"})
        self.assertEqual((code, b["created"], b["duplicate_of"], "job" in b), (200, False, a["id"], False))
        c = self.conn()
        self.assertEqual(len(store.list_docs(c, "roles")), 1)
        c.close()

    def test_adding_a_role_teaches_learning_once(self):
        for _ in range(3):
            self.post({"company": "Contoso", "title": "Director of Program Delivery", "posting": "x", "build": False})
        self.post({"company": "Fabrikam", "title": "Director of Program Delivery", "posting": "x", "build": False})
        c = self.conn()
        sigs = [d["data"] for d in store.list_docs(c, "signals")]
        model = store.get(c, "learned", "model")["data"]
        c.close()
        self.assertEqual([(s["kind"], s["action"], s["source"]) for s in sigs], [("positive", "added_manually", "manual")] * 2)
        self.assertIn("delivery", model["pos_terms"])
        from cc import learned
        self.assertEqual(learned.source_hints(model["outcomes"]), [])

    def test_bad_input_gets_clean_errors(self):
        ok = {"company": "A", "title": "B", "posting": "x"}
        bad = [{}, [1], "x", dict(ok, company=""), dict(ok, title="  "), dict(ok, company=5), dict(ok, posting=5),
               dict(ok, company="x" * 201), dict(ok, link="javascript:alert(1)"), dict(ok, link="ftp://a/b"),
               dict(ok, link="https://a/b c"), {"company": "A", "title": "B"}]
        for body in bad:
            code, out = self.post(body)
            self.assertEqual(code, 400, body)
            self.assertIn("error", out)
        self.assertEqual(self.post(None, raw=b"{nope")[0], 400)
        self.assertEqual(self.post(None, raw=b'{"company":"A","title":"B","posting":"' + b"x" * (300 << 10) + b'"}')[0], 413)
        self.assertEqual(self.post(ok, host="evil.example")[0], 403)
        c = self.conn()
        self.assertEqual(store.list_docs(c, "roles"), [])
        c.close()


if __name__ == "__main__":
    unittest.main()
