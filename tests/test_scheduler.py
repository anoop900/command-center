"""Wizard step 5: the OS scheduler is driven through a fake runner, so no test touches a real Task Scheduler/cron."""
import http.client
import json
import plistlib
import tempfile
import threading
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

from cc import api, cli, config, scheduler, store, web


_DEFAULT_NAMES = mock.patch.object(scheduler, "scope", return_value="")  # these tests cover the default-folder names; test_scope.py covers the rest


def setUpModule():
    _DEFAULT_NAMES.start()


def tearDownModule():
    _DEFAULT_NAMES.stop()

_REAL_PORT_FREE = scheduler._port_free

NS = "{http://schemas.microsoft.com/windows/2004/02/mit/task}"
TIMES = {"morning": "07:30", "midday": "12:30"}


class FakeOS:
    """Just enough schtasks / crontab behaviour: tasks by name, a crontab text."""
    def __init__(self, fail=None):
        self.tasks, self.cron, self.calls, self.fail = {}, "", [], fail

    def __call__(self, argv, input_text=None):
        self.calls.append(argv)
        if self.fail and argv[0] == self.fail:
            return 1, "access denied"
        if argv[0] == "schtasks":
            name = argv[argv.index("/TN") + 1]
            if argv[1] == "/Create":
                self.tasks[name] = Path(argv[argv.index("/XML") + 1]).read_text(encoding="utf-16")
                return 0, ""
            if argv[1] == "/Query":
                return (0, "") if name in self.tasks else (1, "not found")
            if argv[1] in ("/Run", "/End"):
                return 0, ""
            if argv[1] == "/Delete":
                self.tasks.pop(name, None)
                return 0, ""
        if argv[0] == "crontab":
            if argv[1] == "-l":
                return (0, self.cron) if self.cron else (1, "no crontab for user")
            self.cron = input_text
            return 0, ""
        return 1, "unknown"


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        config.set_values(self.d / "config.toml", "mode", {"name": "no-ai"})
        self.cfg = config.load(self.d / "config.toml")

    def tearDown(self):
        self.tmp.cleanup()

    def test_times_are_validated(self):
        for bad in ({}, None, "07:30", {"morning": "7:30", "midday": "12:30"}, {"morning": "24:00", "midday": "12:30"},
                    {"morning": "07:60", "midday": "12:30"}, {"morning": "07:30", "midday": "07:30"},
                    {"morning": 730, "midday": "12:30"}, {"morning": "٠٧:٣٠", "midday": "12:30"},
                    {"morning": "07:30\n", "midday": "12:30"}, {"morning": "07:30", "midday": "12:30 "}):
            with self.assertRaises(scheduler.SchedulerError, msg=repr(bad)):
                scheduler.check_times(bad)
        self.assertEqual(scheduler.check_times({**TIMES, "extra": "x"}), TIMES)

    def test_windows_install_remove_reinstall_leave_exactly_two_tasks(self):
        fake = FakeOS()
        for _ in range(2):
            st = scheduler.install(self.cfg, TIMES, fake, "win32", self.d)
            self.assertTrue(st["all_installed"])
            self.assertEqual(sorted(fake.tasks), ["CommandCenter-midday", "CommandCenter-morning"])
        scheduler.install(self.cfg, {"morning": "06:00", "midday": "13:00"}, fake, "win32", self.d)
        self.assertEqual(len(fake.tasks), 2)
        self.assertIn("T06:00:00", fake.tasks["CommandCenter-morning"])
        self.assertEqual(config.load(self.d / "config.toml").run.midday, "13:00")
        st = scheduler.remove(fake, "win32")
        self.assertEqual((fake.tasks, st["all_installed"]), ({}, False))
        scheduler.remove(fake, "win32")  # nothing to remove is fine
        self.assertFalse(any(c[:2] == ["schtasks", "/Delete"] for c in fake.calls[-3:]))

    def test_windows_task_xml_is_catch_up_least_privilege_and_no_ai(self):
        fake = FakeOS()
        scheduler.install(self.cfg, TIMES, fake, "win32", self.d)
        root = ET.fromstring(fake.tasks["CommandCenter-morning"].split("?>", 1)[1])
        self.assertEqual(root.find(f".//{NS}StartWhenAvailable").text, "true")
        self.assertEqual(root.find(f".//{NS}RunLevel").text, "LeastPrivilege")
        args = root.find(f".//{NS}Arguments").text
        self.assertIn("--no-ai", args)
        self.assertNotIn("--catch-up", args)
        self.assertIn(str(self.d), args)

    def test_autopilot_drops_no_ai_and_other_modes_keep_it(self):
        for mode, has in (("no-ai", True), ("chat", True), ("autopilot", False)):
            argv = scheduler.command("morning", mode, self.d)
            self.assertEqual("--no-ai" in argv, has, mode)
            self.assertEqual(argv[4:6], ["run", "morning"])

    def test_hostile_data_folder_stays_one_argument(self):
        weird = self.d / "a b & 'y' %x%"
        weird.mkdir()
        fake = FakeOS()
        scheduler.install(self.cfg, TIMES, fake, "win32", weird)
        root = ET.fromstring(fake.tasks["CommandCenter-morning"].split("?>", 1)[1])
        args = root.find(f".//{NS}Arguments").text
        self.assertIn('"' + str(weird) + '"', args)  # one quoted argument, & and quotes stay inside it
        self.assertEqual(args.count("run morning"), 1)

    def test_failing_os_call_is_a_clear_error_and_leaves_config_alone(self):
        with self.assertRaises(scheduler.SchedulerError):
            scheduler.install(self.cfg, {"morning": "06:00", "midday": "13:00"}, FakeOS(fail="schtasks"), "win32", self.d)
        self.assertEqual(config.load(self.d / "config.toml").run.morning, "07:30")

    def test_cron_install_keeps_foreign_lines_and_is_idempotent(self):
        fake = FakeOS()
        fake.cron = "MAILTO=me\n0 1 * * * backup\n"
        scheduler.install(self.cfg, TIMES, fake, "linux", self.d)
        first = fake.cron
        scheduler.install(self.cfg, TIMES, fake, "linux", self.d)
        self.assertEqual(fake.cron, first)
        self.assertIn("0 1 * * * backup", first)
        self.assertEqual(first.count("@reboot"), 2)
        self.assertEqual(first.count(scheduler.CRON_BEGIN), 1)
        self.assertIn("30 7 * * *", first)
        self.assertIn("--catch-up", first.split("@reboot")[1])
        self.assertTrue(scheduler.status(fake, "linux")["all_installed"])
        st = scheduler.remove(fake, "linux")
        self.assertEqual((fake.cron, st["all_installed"]), ("MAILTO=me\n0 1 * * * backup\n", False))

    def test_cron_empty_crontab_and_quoting(self):
        fake = FakeOS()
        weird = self.d / "it's here; rm -rf"
        weird.mkdir()
        scheduler.install(self.cfg, TIMES, fake, "linux", weird)
        import shlex
        line = next(x for x in fake.cron.splitlines() if x.startswith("30 7"))
        self.assertIn(str(weird), shlex.split(line))

    def test_mac_plist(self):
        with mock.patch.object(scheduler, "_mac_dir", return_value=self.d / "LA"), \
                mock.patch.object(scheduler.os, "getuid", create=True, return_value=501):
            fake = FakeOS()
            fake.__class__ = type("F", (FakeOS,), {"__call__": lambda s, a, i=None: (s.calls.append(a) or (0, ""))})
            st = scheduler.install(self.cfg, TIMES, fake, "darwin", self.d)
            self.assertTrue(st["all_installed"])
            p = plistlib.loads((self.d / "LA" / "com.commandcenter.morning.plist").read_bytes())
            self.assertEqual(p["StartCalendarInterval"], {"Hour": 7, "Minute": 30})
            self.assertFalse(scheduler.remove(fake, "darwin")["all_installed"])


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name) / "data folder 'x' & y"
        self.d.mkdir()
        p = mock.patch.object(scheduler, "_port_free", return_value=True)   # the real port may be in use here
        p.start()
        self.addCleanup(p.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def test_busy_port_is_refused_before_any_os_call_unless_it_is_our_own(self):
        import socket
        fake = FakeOS()
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        try:
            with mock.patch.object(scheduler, "_port_free", _REAL_PORT_FREE):
                with self.assertRaises(scheduler.SchedulerError) as cm:
                    scheduler.service_install(port, fake, "win32", self.d)
                self.assertIn("--port", str(cm.exception))
                self.assertEqual(fake.calls, [])
                (self.d / scheduler.SERVICE_FILE).write_text('{"port": %d}' % port, encoding="utf-8")
                scheduler.service_install(port, fake, "win32", self.d)   # reinstall of our own port is allowed
        finally:
            s.close()

    def test_windows_task_is_login_triggered_hidden_restarting_and_fixed_port(self):
        fake = FakeOS()
        st = scheduler.service_install(8791, fake, "win32", self.d)
        self.assertTrue(st["installed"])
        xml = fake.tasks["CommandCenter-web"]
        root = ET.fromstring(xml.split("?>", 1)[1])
        self.assertIsNotNone(root.find(f".//{NS}LogonTrigger"))
        self.assertEqual(root.find(f".//{NS}ExecutionTimeLimit").text, "PT0S")
        self.assertEqual(root.find(f".//{NS}RestartOnFailure/{NS}Count").text, "99")
        self.assertEqual(root.find(f".//{NS}RunLevel").text, "LeastPrivilege")
        self.assertIn("--port 8791", root.find(f".//{NS}Arguments").text)
        self.assertTrue(any(c[:2] == ["schtasks", "/Run"] for c in fake.calls))

    def test_reinstall_ends_the_running_instance_before_starting_the_new_one(self):
        fake = FakeOS()
        scheduler.service_install(8791, fake, "win32", self.d)
        verbs = [c[1] for c in fake.calls if c[0] == "schtasks" and c[1] in ("/Create", "/End", "/Run")]
        self.assertEqual(verbs, ["/Create", "/End", "/Run"])  # without /End Task Scheduler keeps the old code serving

    def test_install_is_idempotent_and_leaves_the_run_tasks_alone(self):
        fake = FakeOS()
        cfg_path = self.d / "config.toml"
        config.set_values(cfg_path, "mode", {"name": "no-ai"})
        scheduler.install(config.load(cfg_path), TIMES, fake, "win32", self.d)
        for _ in range(2):
            scheduler.service_install(8791, fake, "win32", self.d)
        self.assertEqual(sorted(fake.tasks), ["CommandCenter-midday", "CommandCenter-morning", "CommandCenter-web"])
        st = scheduler.service_remove(fake, "win32", self.d)
        self.assertFalse(st["installed"])
        self.assertEqual(sorted(fake.tasks), ["CommandCenter-midday", "CommandCenter-morning"])
        scheduler.service_remove(fake, "win32", self.d)   # nothing to remove is fine
        scheduler.remove(fake, "win32")
        self.assertEqual(fake.tasks, {})

    def test_hostile_data_folder_stays_one_argument(self):
        argv = scheduler.service_command(self.d, 8791)
        self.assertEqual(argv[3], str(self.d))
        self.assertEqual(argv[-4:], ["web", "--port", "8791", "--watch"])

    def test_bad_ports_are_refused_before_any_os_call(self):
        fake = FakeOS()
        for bad in (0, 80, 1023, 65536, -1, True, "8791", None, 8791.0):
            with self.assertRaises(scheduler.SchedulerError, msg=repr(bad)):
                scheduler.service_install(bad, fake, "win32", self.d)
        self.assertEqual(fake.calls, [])

    def test_failing_os_call_is_a_clear_error_and_records_nothing(self):
        with self.assertRaises(scheduler.SchedulerError):
            scheduler.service_install(8791, FakeOS(fail="schtasks"), "win32", self.d)
        self.assertFalse((self.d / scheduler.SERVICE_FILE).exists())

    def test_status_reports_whether_the_dashboard_answers(self):
        fake = FakeOS()
        srv = web.make_server(self.d, 0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            st = scheduler.service_install(srv.server_address[1], fake, "win32", self.d)
            self.assertTrue(st["running"])
        finally:
            srv.shutdown()
            srv.server_close()
        self.assertFalse(scheduler.service_status(fake, "win32", self.d)["running"])

    def test_cron_block_is_separate_from_the_run_block(self):
        fake = FakeOS()
        fake.cron = "MAILTO=x\n"
        cfg_path = self.d / "config.toml"
        config.set_values(cfg_path, "mode", {"name": "no-ai"})
        scheduler.install(config.load(cfg_path), TIMES, fake, "linux", self.d)
        scheduler.service_install(8791, fake, "linux", self.d)
        scheduler.service_install(8791, fake, "linux", self.d)
        self.assertEqual(fake.cron.count(scheduler.SVC_BEGIN), 1)
        self.assertEqual(fake.cron.count(scheduler.CRON_BEGIN), 1)
        scheduler.service_remove(fake, "linux", self.d)
        self.assertNotIn(scheduler.SVC_BEGIN, fake.cron)
        self.assertIn(scheduler.CRON_BEGIN, fake.cron)
        self.assertIn("MAILTO=x", fake.cron)

    def test_mac_plist_keeps_alive(self):
        p = plistlib.loads(scheduler.service_plist(["a", "b"]))
        self.assertEqual((p["RunAtLoad"], p["KeepAlive"], p["ProgramArguments"]), (True, True, ["a", "b"]))

    def test_taken_port_fails_loudly_into_the_log(self):
        import socket
        import subprocess
        import sys
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        try:
            port = s.getsockname()[1]
            argv = [sys.executable] + scheduler.service_command(self.d, port)[1:]
            (self.d / "logs").mkdir()
            r = subprocess.run(argv, capture_output=True, text=True, timeout=60, cwd=str(Path(scheduler.__file__).parent.parent))
        finally:
            s.close()
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("cannot listen", (self.d / "logs" / "web.log").read_text())

    def test_cli_usage_and_bad_port(self):
        for args in ([], ["bogus"], ["install", "--port"], ["install", "--port", "abc"], ["install", "--port", "80"]):
            with mock.patch("sys.stderr"):
                self.assertIn(cli._service(args), (1, 2), args)
        with mock.patch.object(scheduler, "service_status", return_value={"installed": False, "running": False}), \
                mock.patch("sys.stdout"):
            self.assertEqual(cli._service(["status"]), 0)

    def test_cli_install_without_port_keeps_the_saved_port(self):
        (self.d / scheduler.SERVICE_FILE).write_text('{"port": 8792}', encoding="utf-8")
        with mock.patch.object(cli.config, "data_dir", return_value=self.d), \
                mock.patch.object(scheduler, "service_install", return_value={"running": True}) as inst, \
                mock.patch("sys.stdout"):
            self.assertEqual(cli._service(["install"]), 0)
        self.assertEqual(inst.call_args[0][0], 8792)


class RunHeartbeat(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.env = mock.patch.dict("os.environ", {"CC_HOME": str(self.home)})
        self.env.start()
        cli.main(["init"])

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def last(self):
        conn = store.connect(self.home / "commandcenter.db")
        try:
            return api.last_finished_run(conn)
        finally:
            conn.close()

    def test_status_shows_last_run_after_a_no_ai_run(self):
        self.assertIsNone(self.last())
        with mock.patch("sys.stdout"):
            self.assertEqual(cli.main(["run", "morning", "--no-ai"]), 0)
        self.assertEqual(self.last()["run"], "morning")

    def test_catch_up_runs_once_and_only_when_due(self):
        import datetime as dt
        today = dt.date.today()
        early, late = (dt.datetime.combine(today, dt.time(h, m)) for h, m in ((0, 0), (23, 59)))
        conn = store.connect(self.home / "commandcenter.db")
        try:
            self.assertFalse(cli._due(conn, "morning", early))
            self.assertTrue(cli._due(conn, "morning", late))
            api.run_heartbeat(conn, "morning", "finish")
            self.assertFalse(cli._due(conn, "morning", late))
            self.assertTrue(cli._due(conn, "midday", late))
        finally:
            conn.close()
        with mock.patch("sys.stdout") as out:
            cli.main(["run", "morning", "--no-ai", "--catch-up"])  # done today: skipped, still exits 0
        self.assertIn("skipped", "".join(str(c.args[0]) for c in out.write.call_args_list))


class ScheduleHttp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fake = FakeOS()
        self.srv = web.make_server(self.tmp.name, 0)
        self.srv.RequestHandlerClass.sched_runner = staticmethod(self.fake)
        self.srv.RequestHandlerClass.sched_platform = "win32"
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.RequestHandlerClass.sched_runner = None
        self.srv.RequestHandlerClass.sched_platform = None
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

    def test_service_checkbox_installs_and_removes_the_login_task(self):
        with mock.patch.object(scheduler, "_port_free", return_value=True):
            st, j = self.call("POST", "/wizard/schedule", {"action": "service_install"})
        self.assertEqual((st, j["service"]["installed"], j["service"]["port"]), (200, True, 8791), j)
        self.assertIn("CommandCenter-web", self.fake.tasks)
        self.assertTrue(self.call("GET", "/wizard/schedule")[1]["service"]["installed"])
        st, j = self.call("POST", "/wizard/schedule", {"action": "service_remove"})
        self.assertEqual((st, j["service"]["installed"]), (200, False))
        self.assertEqual(self.fake.tasks, {})

    def test_service_checkbox_busy_port_is_a_clear_400(self):
        with mock.patch.object(scheduler, "_port_free", return_value=False):
            st, j = self.call("POST", "/wizard/schedule", {"action": "service_install"})
        self.assertEqual(st, 400)
        self.assertIn("already in use", j["error"])
        self.assertEqual(self.fake.tasks, {})

    def test_round_trip_and_guards(self):
        st, j = self.call("GET", "/wizard/schedule")
        self.assertEqual((st, j["times"], j["status"]["all_installed"]), (200, TIMES, False))
        self.assertEqual(self.call("POST", "/wizard/schedule", {})[0], 400)
        self.assertEqual(self.call("POST", "/wizard/schedule", {"action": "install", "morning": "9", "midday": "10"})[0], 400)
        self.assertEqual(self.fake.tasks, {})
        st, j = self.call("POST", "/wizard/schedule", {"action": "install", "morning": "06:15", "midday": "13:45"})
        self.assertEqual((st, j["status"]["all_installed"]), (200, True))
        self.assertEqual(self.call("GET", "/wizard/schedule")[1]["times"]["morning"], "06:15")
        self.assertEqual(self.call("POST", "/wizard/schedule", {"action": "install", "morning": "06:15", "midday": "13:45"},
                                   {"Origin": "http://evil.example"})[0], 403)
        self.assertEqual(self.call("GET", "/wizard/schedule", headers={"Host": "evil.example"})[0], 403)
        self.assertEqual(self.call("POST", "/wizard/schedule", raw="{nope")[0], 400)
        st, j = self.call("POST", "/wizard/schedule", {"action": "remove"})
        self.assertEqual((st, j["status"]["all_installed"], self.fake.tasks), (200, False, {}))
        (Path(self.tmp.name) / "config.toml").write_text("[mode\n", encoding="utf-8")
        self.assertEqual(self.call("POST", "/wizard/schedule", {"action": "install", "morning": "06:15", "midday": "13:45"})[0], 400)
        self.assertEqual(self.call("GET", "/wizard/schedule")[0], 200)


if __name__ == "__main__":
    unittest.main()
