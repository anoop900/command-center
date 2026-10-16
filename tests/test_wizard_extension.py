"""Wizard step 6: the extension is copied with the step 3 profile, hand-written files are left alone,
and the status checks say what is missing."""
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from cc import profile, web, wizard


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name) / "data"
        self.d.mkdir()
        self.src = Path(self.tmp.name) / "src"
        (self.src / "test").mkdir(parents=True)
        (self.src / "manifest.json").write_text("{}", encoding="utf-8")
        (self.src / "test" / "mock.html").write_text("x", encoding="utf-8")
        (self.src / "profile.json").write_text("{\"secret\": 1}", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def facts(self, **kw):
        f = {"_source": profile.MARK, "firstName": "Jordan", "lastName": "Example", "email": "j@example.com"}
        f.update(kw)
        (self.d / "profile.json").write_text(json.dumps(f), encoding="utf-8")


class Prepare(Base):
    def test_needs_step_3_first(self):
        with self.assertRaisesRegex(ValueError, "step 3"):
            wizard.prepare_extension(self.d, self.src)
        self.assertFalse((self.d / "extension").exists())

    def test_copies_files_and_profile_but_not_tests_or_stray_profile(self):
        self.facts()
        r = wizard.prepare_extension(self.d, self.src)
        self.assertTrue(r["ready"])
        ext = self.d / "extension"
        self.assertTrue((ext / "manifest.json").is_file())
        self.assertFalse((ext / "test").exists())
        self.assertEqual(json.loads((ext / "profile.json").read_text(encoding="utf-8"))["firstName"], "Jordan")
        self.assertTrue((self.d / "kits").is_dir())
        wizard.prepare_extension(self.d, self.src)  # repeat is fine

    def test_hand_written_profile_is_left_alone(self):
        self.facts()
        (self.d / "extension").mkdir()
        (self.d / "extension" / "profile.json").write_text("{\"mine\": 1}", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "left alone"):
            wizard.prepare_extension(self.d, self.src)

    def test_damaged_or_wrong_shape_profile(self):
        for raw in (b"\xff\xfe\x00", b"[1]", b"{nope"):
            (self.d / "profile.json").write_bytes(raw)
            with self.assertRaisesRegex(ValueError, "damaged"):
                wizard.prepare_extension(self.d, self.src)

    def test_missing_source(self):
        self.facts()
        with self.assertRaisesRegex(ValueError, "not found"):
            wizard.prepare_extension(self.d, self.d / "nope")


class Status(Base):
    def by(self, r):
        return {c["check"]: c for c in r["checks"]}

    def test_before_prepare_nothing_is_ready(self):
        r = wizard.extension_status(self.d, self.src)
        self.assertFalse(r["ready"])
        self.assertFalse(self.by(r)["extension files"]["ok"])

    def test_missing_required_fact_is_named(self):
        self.facts(email="")
        wizard.prepare_extension(self.d, self.src)
        c = self.by(wizard.extension_status(self.d, self.src))["profile"]
        self.assertFalse(c["ok"])
        self.assertIn("email", c["detail"])

    def test_damaged_extension_profile(self):
        self.facts()
        wizard.prepare_extension(self.d, self.src)
        (self.d / "extension" / "profile.json").write_bytes(b"\xff\xfe\x00")
        self.assertIn("damaged", self.by(wizard.extension_status(self.d, self.src))["profile"]["detail"])


class Http(Base):
    def setUp(self):
        super().setUp()
        self.srv = web.make_server(self.d, 0)
        self.srv.RequestHandlerClass.ext_src = self.src
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        super().tearDown()

    def call(self, method, path, body=b"{}", headers=None):
        for attempt in (1, 2):  # Windows loopback occasionally resets a fresh connection
            c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
            try:
                c.request(method, path, body, headers or {})
                r = c.getresponse()
                out = r.read()
                break
            except ConnectionError:
                c.close()
                if attempt == 2:
                    raise
        c.close()
        return r.status, json.loads(out)

    def test_round_trip_and_guards(self):
        self.assertEqual(self.call("GET", "/wizard/extension")[1]["ready"], False)
        st, j = self.call("POST", "/wizard/extension")
        self.assertEqual(st, 400)
        self.assertIn("step 3", j["error"])
        self.facts()
        st, j = self.call("POST", "/wizard/extension")
        self.assertEqual((st, j["ready"]), (200, True))
        self.assertEqual(self.call("POST", "/wizard/extension", b"[1]")[0], 400)
        self.assertEqual(self.call("POST", "/wizard/extension", b"\xff\x00")[0], 400)
        self.assertEqual(self.call("POST", "/wizard/extension", headers={"Origin": "http://evil.example"})[0], 403)
        self.assertEqual(self.call("GET", "/wizard/extension", headers={"Host": "evil.example"})[0], 403)


if __name__ == "__main__":
    unittest.main()
