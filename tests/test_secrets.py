"""Tests for cc/secrets.py with a fake keyring (no real keyring is touched). Run: python -m unittest discover tests"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cc import secrets


class FakeKeyring:
    def __init__(self):
        self.d = {}

    def get_password(self, s, n):
        return self.d.get((s, n))

    def set_password(self, s, n, v):
        self.d[(s, n)] = v

    def delete_password(self, s, n):
        del self.d[(s, n)]


class SecretsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = mock.patch.dict(os.environ, {"CC_HOME": str(Path(self.tmp.name) / "h")})
        p.start()
        self.addCleanup(p.stop)
        os.environ.pop("CC_SECRET_MAIL_PASSWORD", None)

    def use(self, kr):
        p = mock.patch.object(secrets, "_keyring", return_value=kr)
        p.start()
        self.addCleanup(p.stop)

    def test_keyring_round_trip(self):
        kr = FakeKeyring()
        self.use(kr)
        self.assertEqual(secrets.backend(), "keyring")
        self.assertEqual(secrets.set("mail_password", "pw"), "keyring")
        self.assertEqual(secrets.get("mail_password"), "pw")
        self.assertFalse(secrets._file().exists())
        secrets.delete("mail_password")
        self.assertIsNone(secrets.get("mail_password"))

    def test_file_fallback_round_trip(self):
        self.use(None)
        self.assertEqual(secrets.backend(), "file")
        self.assertEqual(secrets.set("mail_password", "pw"), "file")
        self.assertEqual(secrets.get("mail_password"), "pw")
        secrets.delete("mail_password")
        self.assertIsNone(secrets.get("mail_password"))

    def test_env_overrides(self):
        self.use(FakeKeyring())
        secrets.set("mail_password", "stored")
        with mock.patch.dict(os.environ, {"CC_SECRET_MAIL_PASSWORD": "env"}):
            self.assertEqual(secrets.get("mail_password"), "env")

    def test_empty_refused_and_missing_is_none(self):
        self.use(None)
        with self.assertRaises(ValueError):
            secrets.set("x", "")
        self.assertIsNone(secrets.get("x"))


if __name__ == "__main__":
    unittest.main()
