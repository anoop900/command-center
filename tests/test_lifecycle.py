"""cc start / open / uninstall and the self-restart watcher. Every OS call goes through test_scheduler.FakeOS, so no test
touches the real Task Scheduler, cron, Start menu or browser."""
import contextlib
import io
import os
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from cc import cli, lifecycle, scheduler, selfwatch
from tests.test_scheduler import FakeOS


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name) / "data"
        env = mock.patch.dict(os.environ, {"CC_HOME": str(self.d)})
        env.start()
        self.addCleanup(env.stop)
        self.opened = []

    def run_start(self, rest=(), fake=None, free=lambda p: True, reachable=lambda p: True, foreground=None, wait=0.2):
        fake = fake or FakeOS()
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(scheduler, "_port_free", side_effect=free), \
                mock.patch.object(scheduler, "_reachable", side_effect=reachable), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = lifecycle.start(list(rest), fake, wait, foreground, self.opened.append, "win32")
        return code, out.getvalue(), err.getvalue(), fake


class StartTests(Base):
    def test_fresh_install_starts_the_service_opens_the_browser_and_returns(self):
        code, out, _, fake = self.run_start()
        self.assertEqual(code, 0)
        self.assertIn("CommandCenter-web", fake.tasks)
        self.assertEqual(self.opened, ["http://127.0.0.1:8791/"])
        self.assertIn("close this window", out)
        self.assertIn("--watch", fake.tasks["CommandCenter-web"])

    def test_busy_first_port_moves_to_the_next_free_one(self):
        code, out, _, _ = self.run_start(free=lambda p: p != 8791)
        self.assertEqual(code, 0)
        self.assertEqual(self.opened, ["http://127.0.0.1:8792/"])

    def test_no_free_port_fails_with_a_message_and_no_os_call(self):
        code, _, err, fake = self.run_start(free=lambda p: False)
        self.assertEqual(code, 1)
        self.assertEqual(fake.tasks, {})
        self.assertEqual(self.opened, [])
        self.assertIn("No free port", err)

    def test_refused_task_falls_back_to_the_foreground_window(self):
        calls = []
        code, out, _, _ = self.run_start(fake=FakeOS(fail="schtasks"), foreground=lambda r: calls.append(r) or 0)
        self.assertEqual(calls, [[]])
        self.assertIn("Keep this window open", out)
        self.assertEqual(self.opened, [])

    def test_no_foreground_stops_with_an_error_instead_of_waiting(self):
        calls = []
        code, out, _, _ = self.run_start(["--no-foreground"], fake=FakeOS(fail="schtasks"), foreground=lambda r: calls.append(r) or 0)
        self.assertEqual(code, 1)
        self.assertEqual(calls, [])
        self.assertIn("Could not set Command Center to run in the background", out)
        self.assertNotIn("Keep this window open", out)

    def test_service_that_never_answers_does_not_open_a_dead_page(self):
        code, _, err, _ = self.run_start(reachable=lambda p: False)
        self.assertEqual(code, 1)
        self.assertEqual(self.opened, [])
        self.assertIn("web.log", err)

    def test_already_running_is_left_alone_unless_reinstall_is_asked_for(self):
        _, _, _, fake = self.run_start()

        def creates():
            return len([c for c in fake.calls if c[:2] == ["schtasks", "/Create"]])
        n = creates()
        code, _, _, _ = self.run_start(fake=fake)
        self.assertEqual((code, creates()), (0, n))
        self.assertEqual(len(self.opened), 2)
        self.run_start(["--reinstall"], fake=fake)
        self.assertEqual(creates(), n + 1)

    def test_no_open_flag(self):
        self.run_start(["--no-open"])
        self.assertEqual(self.opened, [])

    def test_opener_failure_is_not_fatal(self):
        def boom(_):
            raise RuntimeError("no browser")
        out = io.StringIO()
        with mock.patch.object(scheduler, "_port_free", return_value=True), \
                mock.patch.object(scheduler, "_reachable", return_value=True), contextlib.redirect_stdout(out):
            self.assertEqual(lifecycle.start([], FakeOS(), 0.2, None, boom, "win32"), 0)
        self.assertIn("http://127.0.0.1:8791/", out.getvalue())


class UninstallTests(Base):
    def test_removes_tasks_keeps_data_and_says_where_it_is(self):
        _, _, _, fake = self.run_start()
        (self.d / "profile.json").write_text("{}")
        out = io.StringIO()
        with contextlib.redirect_stdout(out), mock.patch.object(scheduler, "_reachable", return_value=False):
            code = lifecycle.uninstall([], fake, "win32")
        self.assertEqual(code, 0)
        self.assertNotIn("CommandCenter-web", fake.tasks)
        self.assertTrue((self.d / "profile.json").exists())
        self.assertIn(str(self.d), out.getvalue())
        self.assertIn("pipx uninstall cc", out.getvalue())

    def test_nothing_installed_is_fine_and_a_failing_os_call_is_reported_not_raised(self):
        with contextlib.redirect_stdout(io.StringIO()), mock.patch.object(scheduler, "_reachable", return_value=False):
            self.assertEqual(lifecycle.uninstall([], FakeOS(), "win32"), 0)
        err = io.StringIO()
        fake = FakeOS()
        fake.tasks["CommandCenter-web"] = "x"

        def refuse_delete(argv, input_text=None):
            return (1, "access denied") if argv[:2] == ["schtasks", "/Delete"] else fake(argv, input_text)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err), \
                mock.patch.object(scheduler, "_reachable", return_value=False):
            self.assertEqual(lifecycle.uninstall([], refuse_delete, "win32"), 1)
        self.assertIn("Could not remove", err.getvalue())

    def test_delete_data_removes_the_folder_and_saved_secrets_only_when_asked(self):
        _, _, _, fake = self.run_start()
        (self.d / "profile.json").write_text("{}")
        with contextlib.redirect_stdout(io.StringIO()), mock.patch.object(scheduler, "_reachable", return_value=False),                 mock.patch("cc.secrets.delete") as gone:
            self.assertEqual(lifecycle.uninstall(["--delete-data", "--yes"], fake, "win32"), 0)
        self.assertFalse(self.d.exists())
        self.assertEqual({c.args[0] for c in gone.call_args_list}, {"ai_api_key", "mail_password", "calendar_url"})

    def test_delete_data_needs_a_typed_yes(self):
        _, _, _, fake = self.run_start()
        with contextlib.redirect_stdout(io.StringIO()), mock.patch.object(scheduler, "_reachable", return_value=False),                 mock.patch("builtins.input", return_value="no"):
            lifecycle.uninstall(["--delete-data"], fake, "win32")
        self.assertTrue(self.d.exists())

    def test_delete_data_refuses_home_root_and_foreign_folders(self):
        home = Path.home()
        self.assertTrue(lifecycle.data_dir_problem(home))
        self.assertTrue(lifecycle.data_dir_problem(home.anchor))
        self.assertTrue(lifecycle.data_dir_problem(home.parent))
        other = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, other, True)
        (other / "important.txt").write_text("x")
        self.assertIn("does not look like", lifecycle.data_dir_problem(other))
        self.assertTrue(lifecycle.data_dir_problem(other / "missing"))
        (other / "config.toml").write_text("")
        self.assertEqual(lifecycle.data_dir_problem(other), "")


class WatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "a.py").write_text("x = 1\n")

    def test_fingerprint_changes_with_code_and_ignores_caches(self):
        a = selfwatch.fingerprint(self.root)
        (self.root / "__pycache__").mkdir()
        (self.root / "__pycache__" / "a.pyc").write_text("zz")
        (self.root / "notes.txt").write_text("zz")
        self.assertEqual(selfwatch.fingerprint(self.root), a)
        (self.root / "a.py").write_text("x = 22\n")
        self.assertNotEqual(selfwatch.fingerprint(self.root), a)

    def test_change_triggers_once_and_no_change_never(self):
        hits = []
        stop = selfwatch.watch(lambda: hits.append(1), interval=0.05, settle=0.05, root=self.root)
        time.sleep(0.3)
        self.assertEqual(hits, [])
        (self.root / "b.py").write_text("y = 2\n")
        time.sleep(0.5)
        stop.set()
        self.assertEqual(hits, [1])

    def test_unreadable_folder_is_ignored(self):
        self.assertIsNone(selfwatch.fingerprint(self.root / "missing"))
        hits = []
        stop = selfwatch.watch(lambda: hits.append(1), interval=0.05, settle=0.05, root=self.root / "missing")
        time.sleep(0.2)
        stop.set()
        self.assertEqual(hits, [])

    def test_exit_code_on_windows_and_mac_and_respawn_elsewhere(self):
        with mock.patch.object(selfwatch.sys, "platform", "win32"), mock.patch.object(selfwatch.subprocess, "call", return_value=0) as c:
            self.assertEqual(selfwatch.respawn_if_needed(["py", "-c", "boot"]), 0)
            self.assertEqual(c.call_args[0][0], ["py", "-c", "boot"])
        with mock.patch.object(selfwatch.sys, "platform", "win32"), mock.patch.object(selfwatch.subprocess, "call", side_effect=OSError):
            self.assertEqual(selfwatch.respawn_if_needed(["x"]), selfwatch.EXIT_RESTART)
        with mock.patch.object(selfwatch.sys, "platform", "darwin"):
            self.assertEqual(selfwatch.respawn_if_needed(["x"]), selfwatch.EXIT_RESTART)
        with mock.patch.object(selfwatch.sys, "platform", "linux"), mock.patch.object(selfwatch.subprocess, "Popen") as p:
            self.assertEqual(selfwatch.respawn_if_needed(["py", "-c", "boot"]), 0)
            self.assertEqual(p.call_args[0][0], ["py", "-c", "boot"])

    def test_service_command_carries_watch_and_the_plain_one_does_not(self):
        self.assertIn("--watch", scheduler.service_command(self.root, 8791))
        self.assertNotIn("--watch", scheduler.service_command(self.root, 8791, watch=False))


class WebRestartTests(Base):
    def test_cc_web_with_watch_stops_and_returns_the_restart_code_when_code_changes(self):
        code_dir = Path(self.tmp.name) / "code"
        code_dir.mkdir()
        (code_dir / "m.py").write_text("a = 1\n")
        real = selfwatch.watch
        spawned = []
        res = []
        with mock.patch.object(selfwatch, "watch", side_effect=lambda cb: real(cb, interval=0.05, settle=0.05, root=code_dir)), \
                mock.patch.object(selfwatch, "respawn_if_needed", side_effect=lambda argv: spawned.append(argv) or 3), \
                contextlib.redirect_stdout(io.StringIO()):
            t = threading.Thread(target=lambda: res.append(cli.main(["web", "--port", "0", "--watch"])))
            t.start()
            time.sleep(0.5)
            (code_dir / "m.py").write_text("a = 2\n")
            t.join(10)
        self.assertFalse(t.is_alive())
        self.assertEqual(res, [3])
        self.assertIn("--watch", spawned[0])


if __name__ == "__main__":
    unittest.main()
