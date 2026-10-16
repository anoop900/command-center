"""Wizard step 2: live mail and calendar tests, saving, and what must never leak or be stored on failure."""
import http.client
import imaplib
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from cc import config, wizard, web

PW = "s3cretpassw0rd"
URL = "https://calendar.example.com/private/abc123secret/basic.ics"
ICS = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nEND:VCALENDAR\r\n"


class FakeImap:
    def __init__(self, login_error=None, select_typ="OK"):
        self.login_error, self.select_typ, self.calls = login_error, select_typ, []

    def login(self, user, pw):
        self.calls.append("login")
        if self.login_error:
            raise imaplib.IMAP4.error(self.login_error.format(pw=pw, user=user))

    def select(self, mailbox="INBOX", readonly=False):
        self.calls.append(("select", readonly))
        return self.select_typ, [b"1"]

    def logout(self):
        self.calls.append("logout")

    def store(self, *a):
        raise AssertionError("write command reached the server")


class MailChecks(unittest.TestCase):
    def test_ok_and_read_only(self):
        f = FakeImap()
        self.assertTrue(wizard.check_mail("imap.example.com", "me@example.com", "INBOX", PW, lambda h: f)["ok"])
        self.assertIn(("select", True), f.calls)

    def test_bad_login_message_never_contains_password_or_user(self):
        f = FakeImap(login_error="LOGIN failed for {user} with {pw}")
        with self.assertRaises(ValueError) as cm:
            wizard.check_mail("imap.example.com", "me@example.com", "INBOX", PW, lambda h: f)
        self.assertNotIn(PW, str(cm.exception))
        self.assertNotIn("me@example.com", str(cm.exception))

    def test_gmail_app_password_error_gets_a_plain_hint(self):
        f = FakeImap(login_error="[ALERT] Application-specific password required: https://support.google.com/accounts/answer/185833 (Failure)")
        with self.assertRaises(ValueError) as cm:
            wizard.check_mail("imap.gmail.com", "me@example.com", "INBOX", PW, lambda h: f)
        self.assertIn("myaccount.google.com/apppasswords", str(cm.exception))
        self.assertNotIn(PW, str(cm.exception))

    def test_calendar_404_gets_a_hint_and_hides_the_link(self):
        def fetch(u):
            raise OSError("HTTP Error 404: Not Found")
        with self.assertRaises(ValueError) as cm:
            wizard.check_calendar(URL, fetch)
        self.assertIn("Secret address in iCal format", str(cm.exception))
        self.assertNotIn("abc123secret", str(cm.exception))

    def test_unreachable_server_and_missing_folder(self):
        def boom(h):
            raise OSError("timed out")
        with self.assertRaises(ValueError):
            wizard.check_mail("imap.example.com", "me@example.com", "INBOX", PW, boom)
        with self.assertRaises(ValueError):
            wizard.check_mail("imap.example.com", "me@example.com", "Nope", PW, lambda h: FakeImap(select_typ="NO"))

    def test_hostile_fields(self):
        ok = ("imap.example.com", "me@example.com", "INBOX", PW)
        bad_hosts = ["", None, 5, "localhost", "http://imap.example.com", "imap.example.com:993", "a b.com",
                     "evil.com/x", "x" * 300 + ".com", "imap.example.com\n", "-bad.example.com", "\x00.com"]
        for h in bad_hosts:
            with self.assertRaises(ValueError, msg=repr(h)):
                wizard.check_mail(h, *ok[1:], opener=lambda h: FakeImap())
        for u in ['a"b@example.com', "a b@example.com", "me\n@example.com", "", None, 7, "x" * 300]:
            with self.assertRaises(ValueError, msg=repr(u)):
                wizard.check_mail(ok[0], u, "INBOX", PW, lambda h: FakeImap())
        for p in ["", None, 5, "line\nbreak", "x" * 300]:
            with self.assertRaises(ValueError, msg=repr(p)):
                wizard.check_mail(ok[0], ok[1], "INBOX", p, lambda h: FakeImap())
        with self.assertRaises(ValueError):
            wizard.check_mail(ok[0], ok[1], 'IN"BOX', PW, lambda h: FakeImap())


class SaveChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = Path(self.tmp.name) / "config.toml"
        self.cfg.write_text(config.STARTER, encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_save_mail_stores_password_only_in_secrets(self):
        with mock.patch("cc.secrets.set", return_value="keyring") as ss:
            r = wizard.save_mail(self.cfg, "IMAP.Example.com", "me@example.com", "", PW, lambda h: FakeImap())
        ss.assert_called_once_with("mail_password", PW)
        self.assertEqual(r["stored_in"], "keyring")
        text = self.cfg.read_text(encoding="utf-8")
        self.assertNotIn(PW, text)
        m = config.load(self.cfg).mail
        self.assertEqual((m.host, m.user, m.folder), ("imap.example.com", "me@example.com", "INBOX"))

    def test_failed_test_saves_nothing(self):
        before = self.cfg.read_text(encoding="utf-8")
        with mock.patch("cc.secrets.set") as ss:
            with self.assertRaises(ValueError):
                wizard.save_mail(self.cfg, "imap.example.com", "me@example.com", "INBOX", PW,
                                 lambda h: FakeImap(login_error="no"))
        ss.assert_not_called()
        self.assertEqual(self.cfg.read_text(encoding="utf-8"), before)

    def test_save_into_corrupt_config_fails_cleanly(self):
        self.cfg.write_text("[mail\nhost=", encoding="utf-8")
        with mock.patch("cc.secrets.set"):
            with self.assertRaises(ValueError):
                wizard.save_mail(self.cfg, "imap.example.com", "me@example.com", "INBOX", PW, lambda h: FakeImap())
        self.assertEqual(self.cfg.read_text(encoding="utf-8"), "[mail\nhost=")

    def test_set_values_rejects_injection_and_secret_keys(self):
        for bad in ['x"\n[mode]\nname="autopilot', "a\\b", "line\nbreak"]:
            with self.assertRaises(config.ConfigError):
                config.set_values(self.cfg, "mail", {"user": bad})
        with self.assertRaises(config.ConfigError):
            config.set_values(self.cfg, "mail", {"password": "x"})
        self.assertEqual(self.cfg.read_text(encoding="utf-8"), config.STARTER)

    def test_calendar(self):
        with self.assertRaises(ValueError):
            wizard.check_calendar("http://example.com/a.ics", lambda u: ICS)
        for bad in ["file:///etc/passwd", "ftp://x/y", "", None, 5, "https://x/" + "a" * 3000, "https://x/\n"]:
            with self.assertRaises(ValueError, msg=repr(bad)):
                wizard.check_calendar(bad, lambda u: ICS)
        with self.assertRaises(ValueError):
            wizard.check_calendar(URL, lambda u: "<html>not a feed</html>")

        def boom(u):
            raise OSError("failed fetching " + u)
        with self.assertRaises(ValueError) as cm:
            wizard.check_calendar(URL, boom)
        self.assertNotIn("abc123secret", str(cm.exception))
        with mock.patch("cc.secrets.set", return_value="file") as ss:
            r = wizard.save_calendar(self.cfg, URL, lambda u: ICS)
        ss.assert_called_once_with("calendar_url", URL)
        self.assertTrue(r["ok"] and config.load(self.cfg).calendar.ics_configured)
        self.assertNotIn("abc123secret", self.cfg.read_text(encoding="utf-8"))


class StepTwoHttp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.srv = web.make_server(self.tmp.name, 0)
        self.srv.RequestHandlerClass.mail_opener = staticmethod(lambda h: FakeImap())
        self.srv.RequestHandlerClass.cal_fetcher = staticmethod(lambda u: ICS)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.tmp.cleanup()

    def call(self, method, path, body=None, headers=None):
        for attempt in (1, 2):  # Windows loopback occasionally resets a fresh connection
            c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
            try:
                c.request(method, path, json.dumps(body if body is not None else {}), headers or {})
                r = c.getresponse()
                out = r.read()
                break
            except ConnectionError:
                c.close()
                if attempt == 2:
                    raise
        c.close()
        return r.status, out

    def test_flow_and_no_echo(self):
        st, out = self.call("GET", "/wizard/providers")
        self.assertEqual(st, 200)
        self.assertIn("gmail", json.loads(out))
        body = {"host": "imap.example.com", "user": "me@example.com", "folder": "INBOX", "password": PW}
        with mock.patch("cc.secrets.set", return_value="keyring"):
            for path in ("/wizard/mail/test", "/wizard/mail"):
                st, out = self.call("POST", path, body)
                self.assertEqual(st, 200)
                self.assertNotIn(PW.encode(), out)
            st, out = self.call("POST", "/wizard/calendar", {"url": URL})
            self.assertEqual(st, 200)
            self.assertNotIn(b"abc123secret", out)
        c = config.load(Path(self.tmp.name) / "config.toml")
        self.assertEqual((c.mail.host, c.calendar.ics_configured), ("imap.example.com", True))

    def test_hostile_and_guarded(self):
        self.assertEqual(self.call("POST", "/wizard/mail/test", {"host": 5})[0], 400)
        self.assertEqual(self.call("POST", "/wizard/mail/test", {})[0], 400)
        self.assertEqual(self.call("POST", "/wizard/calendar/test", {"url": "http://x/"})[0], 400)
        evil = {"Origin": "http://evil.example"}
        self.assertEqual(self.call("POST", "/wizard/mail", {"host": "imap.example.com"}, evil)[0], 403)
        for step in (-1, 7, 99, "2", None, True, [2]):
            self.assertEqual(self.call("POST", "/wizard/skip", {"step": step})[0], 400, repr(step))
        self.assertEqual(self.call("POST", "/wizard/skip", {"step": 2})[0], 200)


if __name__ == "__main__":
    unittest.main()
