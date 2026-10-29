"""Two installs on one PC (different CC_HOME) must never replace each other's tasks, plist files or crontab blocks."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cc import config, scheduler
from tests.test_scheduler import FakeOS


class ScopeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.a, self.b = Path(self.tmp.name) / "private", Path(self.tmp.name) / "public"
        for d in (self.a, self.b):
            d.mkdir()
            (d / "logs").mkdir()

    def test_default_folder_keeps_the_old_names_and_others_get_a_stable_suffix(self):
        self.assertEqual(scheduler.scope(Path.home() / ".commandcenter"), "")
        self.assertEqual(scheduler.scope(self.a), scheduler.scope(str(self.a)))
        self.assertRegex(scheduler.scope(self.a), r"^-[0-9a-f]{8}$")
        self.assertNotEqual(scheduler.scope(self.a), scheduler.scope(self.b))

    def test_windows_services_of_two_folders_coexist_and_remove_only_their_own(self):
        fake = FakeOS()
        with mock.patch.object(scheduler, "_port_free", return_value=True), \
                mock.patch.object(scheduler, "_reachable", return_value=False):
            scheduler.service_install(8791, fake, "win32", self.a)
            scheduler.service_install(8792, fake, "win32", self.b)
        self.assertEqual(len([t for t in fake.tasks if t.startswith("CommandCenter-web")]), 2)
        scheduler.service_remove(fake, "win32", self.a)
        self.assertEqual(list(fake.tasks), ["CommandCenter-web" + scheduler.scope(self.b)])
        self.assertTrue(scheduler.service_status(fake, "win32", self.b)["installed"])
        self.assertFalse(scheduler.service_status(fake, "win32", self.a)["installed"])

    def test_run_tasks_of_two_folders_coexist(self):
        fake = FakeOS()
        cfg = config.load(self.a / "config.toml")
        scheduler.install(cfg, {"morning": "06:00", "midday": "12:00"}, fake, "win32", self.a)
        scheduler.install(cfg, {"morning": "07:00", "midday": "13:00"}, fake, "win32", self.b)
        self.assertEqual(len(fake.tasks), 4)
        scheduler.remove(fake, "win32", self.a)
        self.assertEqual(len(fake.tasks), 2)
        self.assertTrue(scheduler.status(fake, "win32", self.b)["all_installed"])

    def test_cron_blocks_of_two_folders_coexist_and_reinstall_is_idempotent(self):
        fake = FakeOS()
        with mock.patch.object(scheduler, "_port_free", return_value=True), \
                mock.patch.object(scheduler, "_reachable", return_value=False):
            for _ in range(2):
                scheduler.service_install(8791, fake, "linux", self.a)
            scheduler.service_install(8792, fake, "linux", self.b)
        self.assertEqual(fake.cron.count("web begin"), 2)
        scheduler.service_remove(fake, "linux", self.a)
        self.assertEqual(fake.cron.count("web begin"), 1)
        self.assertTrue(scheduler.service_status(fake, "linux", self.b)["installed"])


if __name__ == "__main__":
    unittest.main()
