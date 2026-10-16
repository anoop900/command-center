"""Ask: cc.ai.features.ask_question and POST /ask. Recorded answers, no live AI."""
import json
import os
import unittest
from unittest import mock

from cc import ai
from cc.ai import features, runners
from cc.kits.pipeline import KitError
from tests import test_web


def says(d):
    return lambda task, prompt: json.dumps(d)


class AskFeature(unittest.TestCase):
    def test_answer_and_prompt_fencing(self):
        seen = []

        def r(task, prompt):
            seen.append(prompt)
            return json.dumps({"answer": "Two roles."})
        out = features.ask_question("What next?", "Roles: a, b", "autopilot", r)
        self.assertEqual(out, {"status": "ok", "answer": "Two roles."})
        self.assertIn("What next?", seen[0])
        self.assertIn("Roles: a, b", seen[0])

    def test_no_ai_and_paste(self):
        self.assertEqual(features.ask_question("q", None, "no-ai", ai.none_runner)["status"], "unavailable")
        self.assertEqual(features.ask_question("q", None, "autopilot", lambda t, p: ai.QUEUED)["status"], "waiting")

    def test_bad_input_and_bad_output(self):
        for q in ("", "  ", None, 5, "x" * 4001):
            with self.assertRaises(KitError):
                features.ask_question(q, None, "autopilot", says({"answer": "a"}))
        for c in (5, ["x"], "x" * 30_001):
            with self.assertRaises(KitError):
                features.ask_question("q", c, "autopilot", says({"answer": "a"}))
        for bad in ("garbage", "[]", '{"answer": 5}', '{"answer": "x", "injection_suspect": true}', ""):
            out = features.ask_question("q", None, "autopilot", lambda t, p, b=bad: b)
            self.assertEqual(out["status"], "unavailable", bad)


class AskWeb(unittest.TestCase):
    call = test_web.WebTests.call
    def post(self, body, **kw):
        c = self.call("POST", "/ask", body, kw.pop("headers", {"Content-Type": "application/json"}), **kw)
        return c

    def runner(self, d):
        return mock.patch.object(runners, "make_runner", lambda cfg, conn: says(d))

    def setUp(self):
        test_web.WebTests.setUp(self)
        self.env = mock.patch.dict(os.environ, {"CC_HOME": str(self.dir)})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        test_web.WebTests.tearDown(self)

    def test_answer(self):
        with self.runner({"answer": "Yes."}), mock.patch("cc.config.load") as ld:
            ld.return_value.mode.name = "autopilot"
            code, r = self.post({"question": "ok?", "context": "data"})
        self.assertEqual((code, r), (200, {"status": "ok", "answer": "Yes."}))

    def test_no_ai_is_a_clean_answer(self):
        code, r = self.post({"question": "ok?"})
        self.assertEqual(code, 200)
        self.assertEqual(r["status"], "unavailable")

    def test_hostile_requests(self):
        self.assertEqual(self.post({"question": ""})[0], 400)
        self.assertEqual(self.post({"question": 5})[0], 400)
        self.assertEqual(self.post({"question": "q", "context": 5})[0], 400)
        self.assertEqual(self.post(["q"])[0], 400)
        self.assertEqual(self.post(None)[0], 400)
        self.assertEqual(self.post({"question": "x" * 4001})[0], 400)
        self.assertEqual(self.post({"question": "q", "context": "x" * 300_000})[0], 413)
        self.assertEqual(self.post({"question": "q"}, host="evil.example")[0], 403)
        self.assertEqual(self.post({"question": "q"}, headers={"Origin": "http://evil.example"})[0], 403)
        self.assertEqual(self.call("POST", "/api/tasks/t1", {"title": "a"})[0], 404)
        self.assertEqual(self.call("GET", "/ask")[0], 404)

    def test_server_survives_runner_crash(self):
        def boom(cfg, conn):
            raise ai.RunnerError("no runner configured")
        with mock.patch.object(runners, "make_runner", boom):
            code, r = self.post({"question": "q"})
        self.assertEqual((code, r["status"]), (200, "unavailable"))
        self.assertEqual(self.call("GET", "/")[0], 200)


if __name__ == "__main__":
    unittest.main()
