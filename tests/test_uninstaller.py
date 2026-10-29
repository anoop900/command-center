"""Tests for bootstrap/uninstall.ps1 (Windows only). Fake cc, python and winget programs on a controlled PATH log their
arguments, and LOCALAPPDATA, USERPROFILE and CC_HOME point into a temp folder, so nothing real is ever removed."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from cc import scheduler

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "bootstrap" / "uninstall.ps1"
WIN = os.name == "nt"


@unittest.skipUnless(WIN, "uninstall.ps1 is the Windows uninstaller")
class UninstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.t = Path(self.tmp.name)
        self.bin, self.local, self.home, self.data = (self.t / n for n in ("bin", "local", "home", "data"))
        for d in (self.bin, self.local / "CommandCenter", self.home, self.data):
            d.mkdir(parents=True)
        (self.data / "config.toml").write_text("")
        self.log = self.t / "calls.txt"
        sysroot = os.environ.get("SystemRoot", r"C:\Windows")
        self.path = os.pathsep.join([str(self.bin), str(Path(sysroot) / "System32"), str(Path(sysroot) / "System32" / "WindowsPowerShell" / "v1.0")])
        self.tasklog = self.t / "tasks.txt"
        self.fake_schtasks()      # always faked: the real Task Scheduler must never be touched by a test

    def fake_schtasks(self, tasks=()):
        q = "\r\n".join(f'echo "\\{n}","N/A","Ready"' for n in tasks) or "rem none"
        lines = ["@echo off", f'echo schtasks %* >> "{self.tasklog}"', 'if /i "%1"=="/Query" (', q, ")", "exit /b 0"]
        (self.bin / "schtasks.cmd").write_bytes(("\r\n".join(lines) + "\r\n").encode("ascii"))

    def task_calls(self):
        return [l.strip() for l in self.tasklog.read_text().splitlines()] if self.tasklog.exists() else []

    def fake(self, name, code=0):
        (self.bin / (name + ".cmd")).write_bytes(f'@echo off\r\necho {name} %* >> "{self.log}"\r\nexit /b {code}\r\n'.encode("ascii"))

    def record(self, **kw):
        (self.local / "CommandCenter" / "install-record.json").write_text(json.dumps(kw))

    def run_script(self, *args):
        env = {k: v for k, v in os.environ.items() if k.upper() not in ("PYTHONPATH", "CC_HOME")}
        env.update(PATH=self.path, LOCALAPPDATA=str(self.local), USERPROFILE=str(self.home), CC_HOME=str(self.data))
        p = subprocess.run([shutil.which("powershell.exe"), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT), *args],
                           capture_output=True, text=True, env=env, timeout=120, input="")
        return p.returncode, p.stdout + p.stderr

    def calls(self):
        lines = [l.strip() for l in self.log.read_text().splitlines()] if self.log.exists() else []
        return [l for l in lines if l != 'python -c "import sys"']      # the version probe is not an action

    def test_dry_run_lists_recorded_tools_and_changes_nothing(self):
        self.record(python=True, pipx=True, libreoffice=True)
        for n in ("cc", "python", "winget"):
            self.fake(n)
        code, out = self.run_script("-DryRun", "-Yes", "-DeleteData")
        self.assertEqual(code, 0, out)
        for s in ("WOULD: stop the dashboard", "WOULD: remove the Command Center program", "WOULD: winget uninstall LibreOffice",
                  "WOULD: remove pipx", "WOULD: winget uninstall Python 3.12", "WOULD: save a zip copy"):
            self.assertIn(s, out)
        self.assertEqual(self.calls(), [])
        self.assertTrue((self.data / "config.toml").exists())
        self.assertTrue((self.local / "CommandCenter" / "install-record.json").exists())

    def test_without_a_record_python_pipx_and_libreoffice_are_left_alone(self):
        for n in ("cc", "python", "winget"):
            self.fake(n)
        code, out = self.run_script("-Yes", "-KeepData")
        self.assertEqual(code, 0, out)
        self.assertFalse(any(c.startswith("winget") for c in self.calls()), self.calls())
        self.assertFalse(any("pip uninstall" in c for c in self.calls()))
        self.assertIn("uninstall", self.calls()[0])      # cc uninstall still ran
        self.assertIn("-m pipx uninstall cc", " ".join(self.calls()))

    def test_recorded_libreoffice_is_removed_and_the_record_deleted_on_success(self):
        self.record(libreoffice=True)
        for n in ("cc", "python", "winget"):
            self.fake(n)
        code, out = self.run_script("-Yes", "-KeepData")
        self.assertEqual(code, 0, out)
        self.assertIn("winget uninstall --id TheDocumentFoundation.LibreOffice --silent", self.calls())
        self.assertFalse((self.local / "CommandCenter").exists())

    def test_delete_data_zips_first_then_passes_delete_flags_to_cc(self):
        for n in ("cc", "python"):
            self.fake(n)
        code, out = self.run_script("-Yes", "-DeleteData")
        self.assertEqual(code, 0, out)
        c = self.calls()
        self.assertTrue(c[0].startswith("cc backup create") and c[0].endswith(".zip"), c)
        self.assertEqual(c[1], "cc uninstall --delete-data --yes")

    def test_failed_zip_means_data_is_not_deleted(self):
        self.fake("cc", 1)
        self.fake("python")
        code, out = self.run_script("-Yes", "-DeleteData")
        self.assertIn("will NOT be deleted", out)
        self.assertNotIn("--delete-data", " ".join(self.calls()))

    def test_a_failing_step_keeps_the_record_and_exits_nonzero(self):
        self.record(libreoffice=True)
        self.fake("cc")
        self.fake("python")
        self.fake("winget", 1)
        code, out = self.run_script("-Yes", "-KeepData")
        self.assertEqual(code, 1, out)
        self.assertTrue((self.local / "CommandCenter" / "install-record.json").exists())

    def test_removed_tools_leave_the_record_and_failed_python_gets_the_admin_hint(self):
        self.record(libreoffice=True, python=True)
        self.fake("cc")
        self.fake("python")
        lines = ["@echo off", f'echo winget %* >> "{self.log}"', "echo %* | findstr /i Python >nul && exit /b 1", "exit /b 0"]
        (self.bin / "winget.cmd").write_bytes(("\r\n".join(lines) + "\r\n").encode("ascii"))
        code, out = self.run_script("-Yes", "-KeepData")
        self.assertEqual(code, 1, out)
        self.assertIn("non-administrator", out)
        rec = json.loads((self.local / "CommandCenter" / "install-record.json").read_text())
        self.assertEqual(rec, {"python": True})

    def test_failed_libreoffice_shows_the_reason_and_the_administrator_hint(self):
        self.record(libreoffice=True)
        self.fake("cc")
        self.fake("python")
        (self.bin / "winget.cmd").write_bytes(b"@echo off\r\necho Uninstall failed with exit code: 1603\r\nexit /b 1\r\n")
        code, out = self.run_script("-Yes", "-KeepData")
        self.assertEqual(code, 1, out)
        self.assertIn("1603", out)
        self.assertIn("administrator permission", out)

    def test_failure_message_includes_the_exit_code(self):
        self.record(libreoffice=True)
        self.fake("cc")
        self.fake("python")
        self.fake("winget", 7)
        code, out = self.run_script("-Yes", "-KeepData")
        self.assertEqual(code, 1, out)
        self.assertIn("(exit code 7)", out)

    def test_a_tool_already_gone_counts_as_removed_and_the_record_is_cleared(self):
        self.record(libreoffice=True, python=True)
        self.fake("cc")
        self.fake("python")
        # uninstall fails, but "winget list" says no installed package (0x8A150014 = -1978335212)
        lines = ["@echo off", f'echo winget %* >> "{self.log}"', 'if /i "%1"=="list" exit /b -1978335212', "exit /b 1"]
        (self.bin / "winget.cmd").write_bytes(("\r\n".join(lines) + "\r\n").encode("ascii"))
        code, out = self.run_script("-Yes", "-KeepData")
        self.assertEqual(code, 0, out)
        self.assertIn("already gone", out)
        self.assertFalse((self.local / "CommandCenter").exists())

    def test_leftover_tasks_are_removed_when_cc_is_missing(self):
        self.fake("python")
        sc = scheduler.scope(self.data)
        # the other install's tasks (the default folder's, and one with another suffix) must survive
        self.fake_schtasks([f"CommandCenter-web{sc}", f"CommandCenter-morning{sc}", "CommandCenter-web", "CommandCenter-web-deadbeef",
                            "SomeoneElsesTask"])
        code, out = self.run_script("-Yes", "-KeepData")
        self.assertEqual(code, 0, out)
        deletes = [c for c in self.task_calls() if "/Delete" in c]
        self.assertEqual(len(deletes), 2, self.task_calls())
        self.assertTrue(all(f"{sc} " in c + " " and "deadbeef" not in c and "SomeoneElsesTask" not in c for c in deletes), deletes)
        self.assertFalse(any(c.split()[-2:-1] == ["CommandCenter-web"] for c in deletes), deletes)

    def test_leftover_tasks_are_removed_when_cc_uninstall_fails(self):
        self.fake("cc", 1)
        self.fake("python")
        self.fake_schtasks(["CommandCenter-web" + scheduler.scope(self.data)])
        code, out = self.run_script("-Yes", "-KeepData")
        self.assertEqual(code, 1, out)
        self.assertTrue(any("/Delete" in c and "CommandCenter-web" in c for c in self.task_calls()), self.task_calls())

    def test_tasks_are_left_to_cc_when_it_succeeds_and_dry_run_deletes_nothing(self):
        self.fake("cc")
        self.fake("python")
        self.fake_schtasks(["CommandCenter-web" + scheduler.scope(self.data)])
        self.assertEqual(self.run_script("-Yes", "-KeepData")[0], 0)
        self.assertEqual(self.task_calls(), [])
        (self.bin / "cc.cmd").unlink()
        code, out = self.run_script("-DryRun", "-Yes", "-KeepData")
        self.assertEqual(code, 0, out)
        self.assertIn("WOULD: remove the scheduled task CommandCenter-web" + scheduler.scope(self.data), out)
        self.assertFalse(any("/Delete" in c for c in self.task_calls()))

    def test_declining_the_first_question_changes_nothing(self):
        self.fake("cc")
        code, out = self.run_script()          # no -Yes and no console input: Read-Host returns nothing
        self.assertEqual(code, 0, out)
        self.assertIn("Nothing was changed", out)
        self.assertEqual(self.calls(), [])


if __name__ == "__main__":
    unittest.main()
