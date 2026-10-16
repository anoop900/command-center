"""Wizard step 4: rules are validated, only the owned sections of scoring.toml change, and the result drives score.decide."""
import http.client
import json
import tempfile
import threading
import tomllib
import unittest
from pathlib import Path

from cc import rules_cfg, web
from cc.rules import score

ROUTE = {"country": "gb", "route": "Skilled Worker", "on_silent": "flag", "on_excludes": "fail",
         "flag_people_management": True, "flag_title_patterns": ["Director", " "], "note": 'Ask "an adviser".'}


def rules(**kw):
    base = {"currency": "usd", "floor_salary": "120,000", "floor_hourly": 80, "flag_within": "0.05", "home_country": "us",
            "remote_countries": ["us", "ca", "US"], "onsite_regions": ["Example County"], "relocation_channels": [],
            "onsite_via_route": True, "routes": [ROUTE]}
    base.update(kw)
    return base


class RulesFiles(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def toml(self):
        return tomllib.loads((self.d / "scoring.toml").read_text(encoding="utf-8"))

    def test_saved_file_drives_the_gates(self):
        rules_cfg.save(self.d, rules())
        cfg = score.load_config(self.d / "scoring.toml")
        post = {"scores": {"technical": 90, "experience": 90, "seniority": 90, "career": 90}, "channel": "x",
                "pay": {"max": 100000, "currency": "USD"}, "location": {"type": "remote", "country": "US"}}
        d = score.decide(post, cfg)
        self.assertFalse(d["eligible"])
        self.assertEqual(d["gates"]["pay"]["verdict"], "FAIL")
        post["pay"] = {"max": 150000, "currency": "USD"}
        self.assertTrue(score.decide(post, cfg)["eligible"])
        self.assertEqual(cfg["work_authorization"][0]["country"], "GB")
        self.assertEqual(cfg["work_authorization"][0]["flag_title_patterns"], ["director"])
        self.assertEqual(cfg["location"]["remote_countries"], ["US", "CA"])

    def test_floors_are_never_defaulted(self):
        self.assertEqual(rules_cfg.load(self.d)["floor_salary"], None)
        for bad in (None, "", "abc", 0, -5, True, float("nan"), float("inf"), 1e12, [1], {}):
            with self.assertRaises(ValueError, msg=repr(bad)):
                rules_cfg.save(self.d, rules(floor_salary=bad))
        self.assertFalse((self.d / "scoring.toml").exists())

    def test_other_sections_and_comments_are_kept(self):
        (self.d / "scoring.toml").write_text(
            '# my notes\n[scoring]\nkit_threshold = 70  # mine\nweights = { a = 1 }\nbands = [[0, "x"]]\n\n'
            '[pay]\ncurrency = "CAD"\nfloor_salary = 1\nfloor_hourly = 1\nfx = { USD = 1.37 }\n\n'
            '[boards]\ninclude = ["director"]\n\n[[work_authorization]]\ncountry = "ZZ"\n', encoding="utf-8")
        before = tomllib.loads((self.d / "scoring.toml").read_text(encoding="utf-8"))
        rules_cfg.save(self.d, rules())
        text = (self.d / "scoring.toml").read_text(encoding="utf-8")
        self.assertIn("# my notes", text)
        self.assertIn("# mine", text)
        t = self.toml()
        self.assertEqual((t["scoring"], t["boards"]), (before["scoring"], before["boards"]))
        self.assertEqual(t["pay"]["fx"], {"USD": 1.37})
        self.assertEqual([r["country"] for r in t["work_authorization"]], ["GB"])

    def test_resave_is_idempotent_and_load_round_trips(self):
        rules_cfg.save(self.d, rules())
        first = (self.d / "scoring.toml").read_bytes()
        rules_cfg.save(self.d, rules_cfg.load(self.d))
        self.assertEqual((self.d / "scoring.toml").read_bytes(), first)
        self.assertEqual(rules_cfg.load(self.d), rules_cfg.validate(rules()))

    def test_corrupt_file_is_left_alone(self):
        p = self.d / "scoring.toml"
        p.write_text("[pay\nbroken", encoding="utf-8")
        with self.assertRaises(ValueError):
            rules_cfg.save(self.d, rules())
        self.assertEqual(p.read_text(encoding="utf-8"), "[pay\nbroken")
        self.assertEqual(rules_cfg.load(self.d), rules_cfg.defaults())
        p.write_bytes(b"\xff\xfe\x00bad")
        with self.assertRaises(ValueError):
            rules_cfg.save(self.d, rules())

    def test_bad_existing_fx_blocks_save(self):
        (self.d / "scoring.toml").write_text('[pay]\nfx = { USD = "x" }\n', encoding="utf-8")
        with self.assertRaises(ValueError):
            rules_cfg.save(self.d, rules())

    def test_country_is_never_assumed(self):
        d = rules_cfg.defaults()
        self.assertEqual((d["home_country"], d["remote_countries"]), ("", []))
        self.assertEqual(rules_cfg.load(self.d)["home_country"], "")
        with self.assertRaises(ValueError):
            rules_cfg.save(self.d, rules(home_country=""))

    def test_percentages_and_country_names_are_accepted(self):
        out = rules_cfg.validate(rules(flag_within="10%", home_country="Canada", remote_countries=["USA", "Mexico", "uk", "US"],
                                       routes=[{**ROUTE, "country": "United States"}]))
        self.assertEqual((out["flag_within"], out["home_country"], out["remote_countries"], out["routes"][0]["country"]),
                         (0.1, "CA", ["US", "MX", "GB"], "US"))
        self.assertEqual(rules_cfg.validate(rules(flag_within="0.15"))["flag_within"], 0.15)
        self.assertEqual(rules_cfg.validate(rules(flag_within="10"))["flag_within"], 0.1)
        o = rules_cfg.validate(rules(currency="Canadian dollars", floor_salary="$150k", floor_hourly="CAD 100/hr"))
        self.assertEqual((o["currency"], o["floor_salary"], o["floor_hourly"]), ("CAD", 150000, 100))
        self.assertEqual(rules_cfg.validate(rules(floor_salary="C$150,000"))["floor_salary"], 150000)
        with self.assertRaises(ValueError) as cm:
            rules_cfg.validate(rules(remote_countries=["Europe"]))
        self.assertIn("is a region", str(cm.exception))

    def test_all_problems_are_reported_at_once(self):
        with self.assertRaises(ValueError) as cm:
            rules_cfg.validate(rules(remote_countries=["Europe"], onsite_regions=["Remote"], flag_within="0.8", currency="bucks"))
        msg = str(cm.exception)
        for part in ("remote countries", "on-site places", "warn within", "currency"):
            self.assertIn(part, msg)

    def test_hostile_input_cannot_inject_toml(self):
        for bad in (rules(currency='US"\n[x]'), rules(home_country="Narnia"), rules(onsite_regions=["a\nb = 1"]),
                    rules(onsite_regions="Ontario"), rules(onsite_regions=["x"] * 21), rules(onsite_regions=[1]),
                    rules(remote_countries=["Europe"]), rules(flag_within=0.9), rules(flag_within="x"),
                    rules(flag_within="0.8"), rules(flag_within="60%"), rules(floor_salary="lots"),
                    rules(onsite_regions=["Remote"]), rules(currency="bucks"),
                    rules(onsite_via_route="yes"), rules(routes=[ROUTE] * 11), rules(routes=["x"]),
                    rules(routes=[{**ROUTE, "on_silent": "maybe"}]), rules(routes=[{**ROUTE, "country": "G"}]),
                    rules(routes=[{**ROUTE, "note": "x" * 301}]), rules(routes=[{**ROUTE, "note": "a\nb"}]),
                    rules(routes=[{**ROUTE, "flag_people_management": "yes"}]), rules(onsite_regions=["\x00"]),
                    "text", None, [], 5):
            with self.assertRaises(ValueError, msg=repr(bad)[:50]):
                rules_cfg.save(self.d, bad)
        self.assertFalse((self.d / "scoring.toml").exists())

    def test_quotes_and_backslashes_stay_data(self):
        rules_cfg.save(self.d, rules(onsite_regions=['A "B" \\ C', "ünï"]))
        self.assertEqual(self.toml()["location"]["onsite_regions"], ['A "B" \\ C', "ünï"])
        self.assertEqual(set(self.toml()), {"scoring", "pay", "location", "work_authorization"})


class RulesHttp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.srv = web.make_server(self.tmp.name, 0)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.tmp.cleanup()

    def call(self, method, path, body=None, headers=None, raw=None):
        for attempt in (1, 2):
            c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
            try:
                c.request(method, path, raw if raw is not None else json.dumps(body if body is not None else {}), headers or {})
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
        st, j = self.call("GET", "/wizard/rules")
        self.assertEqual((st, j["state"]), (200, rules_cfg.defaults()))
        self.assertEqual(self.call("POST", "/wizard/rules", {})[0], 400)
        self.assertEqual(self.call("POST", "/wizard/rules", rules())[0], 200)
        self.assertEqual(self.call("GET", "/wizard/rules")[1]["state"]["floor_salary"], 120000)
        self.assertEqual(self.call("POST", "/wizard/rules", rules(), {"Origin": "http://evil.example"})[0], 403)
        self.assertEqual(self.call("GET", "/wizard/rules", headers={"Host": "evil.example"})[0], 403)
        self.assertEqual(self.call("POST", "/wizard/rules", raw="{not json")[0], 400)
        self.assertEqual(self.call("POST", "/wizard/rules", raw="[1]")[0], 400)
        (Path(self.tmp.name) / "scoring.toml").write_text("[pay", encoding="utf-8")
        self.assertEqual(self.call("POST", "/wizard/rules", rules())[0], 400)
        self.assertEqual(self.call("GET", "/wizard/rules")[0], 200)


if __name__ == "__main__":
    unittest.main()
