import unittest
from pathlib import Path

from cc import config, modes


class ModesTests(unittest.TestCase):
    def test_table_is_complete(self):
        self.assertEqual(len(modes.FEATURES), 15)
        for f in modes.FEATURES:
            for m in modes.MODES:
                self.assertTrue(modes.capability(f, m))

    def test_every_mode_card_has_plain_text_and_ai_modes_say_to_connect_one(self):
        cards = {m["name"]: m for m in modes.table()["modes"]}
        for m in cards.values():
            for k in ("tagline", "summary", "cost", "privacy"):
                self.assertTrue(m[k].strip(), (m["name"], k))
        for name in ("assisted", "autopilot"):
            self.assertIn("connect an AI account", cards[name]["summary"])
        self.assertNotIn("connect", cards["no-ai"]["summary"].lower())

    def test_design_cells(self):
        c = modes.capability
        self.assertEqual(c("fit_scoring", "no-ai"), "keyword score only")
        self.assertEqual(c("kits", "autopilot"), "auto (top roles)")
        self.assertEqual(c("claim_review", "chat"), "paste")
        self.assertEqual(c("linkedin_reading", "autopilot"), "browser-capable AI only")

    def test_non_ai_features_identical_in_every_mode(self):
        for f in modes.FEATURES:
            if not modes.uses_ai(f):
                self.assertEqual(len({modes.capability(f, m) for m in modes.MODES}), 1, f)

    def test_no_ai_runs_deterministic_features_only(self):
        self.assertTrue(modes.is_automatic("fit_scoring", "no-ai"))
        self.assertTrue(modes.is_off("claim_review", "no-ai"))
        self.assertTrue(modes.is_off("phone_dashboard", "autopilot"))

    def test_automatic_vs_user_step(self):
        self.assertTrue(modes.is_automatic("kits", "autopilot"))
        self.assertTrue(modes.needs_user_step("kits", "assisted"))
        self.assertTrue(modes.needs_user_step("fit_scoring", "chat"))
        self.assertFalse(modes.needs_user_step("claim_review", "no-ai"))

    def test_only_autopilot_schedules_ai(self):
        self.assertEqual([m for m in modes.MODES if modes.schedules_ai(m)], ["autopilot"])

    def test_rows_and_errors(self):
        self.assertEqual([r["feature"] for r in modes.rows("chat")], list(modes.FEATURES))
        for bad in (lambda: modes.rows("turbo"), lambda: modes.capability("nope", "chat"),
                    lambda: modes.capability("kits", "")):
            with self.assertRaises(modes.ModeError):
                bad()

    def test_config_default_and_validation(self):
        self.assertEqual(config.Mode().name, modes.DEFAULT)
        self.assertEqual(config.validate({"mode": {"name": "chat"}}).mode.name, "chat")
        with self.assertRaises(config.ConfigError):
            config.validate({"mode": {"name": "turbo"}})

    def test_every_off_cell_has_a_reason_and_none_are_stray(self):
        off = {(f, m) for f in modes.FEATURES for m in modes.MODES
               if modes.is_off(f, m) and modes.capability(f, m) != "later"}
        self.assertEqual(off, set(modes.REASONS))

    def test_panel(self):
        p = modes.panel("no-ai")
        self.assertEqual((p["label"], p["next"]["mode"]), ("No AI", "chat"))
        self.assertEqual([x["label"] for x in p["off"]],
                         ["Claim trace + independent review", "Interview prep from stories",
                          "Cover letter (one page, only when you press the button)",
                          "LinkedIn reading beyond alert emails",
                          "Active learning: an AI suggests title terms to avoid (optional, off until you turn it on)"])
        self.assertEqual([x["label"] for x in p["later"]], ["Phone dashboard"])
        self.assertIsNone(modes.panel("autopilot")["next"])
        self.assertEqual(modes.panel("autopilot")["off"], [])
        self.assertIn("Fit scoring", [x["label"] for x in modes.panel("chat")["on_request"]])
        self.assertEqual([modes.panel(m)["label"] for m in modes.UPGRADE_ORDER],
                         ["No AI", "Chat", "Assisted", "Autopilot"])

    def test_page_has_no_mode_text(self):
        page = (Path(config.__file__).parent.parent / "web" / "index.html").read_text(encoding="utf-8")
        for w in ("Autopilot", "Assisted", "no-ai", "Claim trace"):
            self.assertNotIn(w, page)

    def test_mode_names_not_hardcoded_elsewhere(self):
        for p in (Path(config.__file__).parent).rglob("*.py"):
            if p.name in ("modes.py", "config.py"):
                continue
            text = p.read_text(encoding="utf-8")
            self.assertNotIn('"autopilot"', text, p.name)
            self.assertNotIn('"assisted"', text, p.name)


if __name__ == "__main__":
    unittest.main()
