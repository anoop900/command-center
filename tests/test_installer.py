"""Tests for bootstrap/install.ps1 (Windows only). Run: python -m unittest discover tests

The script is run with a controlled PATH holding fake py/python/winget programs, so no real install happens: dry runs
change nothing, and the one real run only ever calls a fake python that fails on purpose.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "bootstrap" / "install.ps1"
WIN = os.name == "nt"
STORE_STUB = "@echo off\r\necho Python was not found; run without arguments to install from the Microsoft Store 1>&2\r\nexit /b 9009\r\n"
# Reports 3.12 for the version probe, then fails the way pip does when a stray PYTHONPATH points at an unreadable folder.
BROKEN_PIP = ('@echo off\r\nif "%1"=="-c" (echo 3.12& exit /b 0)\r\n'
              "echo PermissionError: [WinError 5] Access is denied: 'C:\\Users\\Other' 1>&2\r\nexit /b 1\r\n")


@unittest.skipUnless(WIN, "install.ps1 is the Windows installer")
class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.t = Path(self.tmp.name)
        self.home = self.t / "home"
        self.bin = self.t / "bin"
        self.bin.mkdir()
        sysroot = os.environ.get("SystemRoot", r"C:\Windows")
        self.ps = shutil.which("powershell.exe")
        self.base_path = os.pathsep.join([str(Path(sysroot) / "System32"), str(Path(sysroot) / "System32" / "WindowsPowerShell" / "v1.0")])

    def put(self, name, text):
        (self.bin / name).write_bytes(text.encode("ascii"))

    def run_script(self, *args, path=None, script=SCRIPT, extra_env=None):
        env = {k: v for k, v in os.environ.items() if k.upper() not in ("PYTHONPATH", "CC_HOME")}
        env["CC_HOME"] = str(self.home)
        if path is not None:
            env["PATH"] = path
        env.update(extra_env or {})
        p = subprocess.run([self.ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), *args],
                           capture_output=True, text=True, env=env, timeout=180)
        return p.returncode, p.stdout + p.stderr

    def stub_path(self, with_winget=False):
        if with_winget:
            self.put("winget.cmd", "@echo off\r\nexit /b 0\r\n")
        return os.pathsep.join([str(self.bin), self.base_path])

    def test_dry_run_numbers_the_steps(self):
        code, out = self.run_script("-DryRun")
        self.assertEqual(code, 0, out)
        for s in ("Step 1 of 5", "Step 2 of 5", "Step 3 of 5", "Step 4 of 5", "WOULD: run cc start --reinstall"):
            self.assertIn(s, out)
        self.assertNotIn("Traceback", out)

    def test_store_stub_without_winget_fails_in_plain_words(self):
        self.put("py.cmd", STORE_STUB)
        self.put("python.cmd", STORE_STUB)
        env = {"LOCALAPPDATA": str(self.t / "nolocal")}      # hide any real Python under the user profile
        code, out = self.run_script("-DryRun", path=self.stub_path(), extra_env=env)
        self.assertEqual(code, 1, out)
        self.assertIn("Something went wrong", out)
        self.assertIn("python.org", out)
        self.assertNotIn("NativeCommandError", out)
        log = self.home / "install-log.txt"
        self.assertTrue(log.is_file())
        self.assertIn("Something went wrong", log.read_text(encoding="utf-8"))

    def test_store_stub_with_winget_takes_the_install_branch(self):
        self.put("py.cmd", STORE_STUB)
        self.put("python.cmd", STORE_STUB)
        env = {"LOCALAPPDATA": str(self.t / "nolocal")}
        code, out = self.run_script("-DryRun", path=self.stub_path(with_winget=True), extra_env=env)
        self.assertEqual(code, 0, out)
        self.assertIn("WOULD: winget install Python 3.12", out)

    def test_dry_run_reports_libreoffice_already_installed(self):
        self.put("soffice.cmd", "@echo off\r\n")
        path = os.pathsep.join([str(self.bin), os.environ["PATH"]])
        code, out = self.run_script("-DryRun", path=path)
        self.assertEqual(code, 0, out)
        self.assertIn("LibreOffice already installed", out)
        self.assertNotIn("WOULD: winget install LibreOffice", out)

    def test_disk_space_line_is_one_sentence(self):
        code, out = self.run_script("-DryRun", "-AssumeFreeMB", "50000")
        self.assertEqual(code, 0, out)
        self.assertRegex(out, r"This install needs about [\d.]+ GB of free disk space\. You have [\d.,]+ GB free\.")

    def test_not_enough_disk_space_stops_before_changing_anything(self):
        code, out = self.run_script("-DryRun", "-AssumeFreeMB", "100")
        self.assertEqual(code, 1, out)
        self.assertIn("not enough free disk space", out)
        self.assertIn("make some space first", out)
        self.assertIn("Nothing was changed", out)
        self.assertNotIn("Step 2 of 5", out)
        self.assertIn("not enough free disk space", (self.home / "install-log.txt").read_text(encoding="utf-8"))

    def test_components_already_present_are_skipped_and_logged(self):
        self.put("soffice.cmd", "@echo off" + chr(13) + chr(10))
        path = os.pathsep.join([str(self.bin), os.environ["PATH"]])
        code, out = self.run_script("-DryRun", path=path)
        self.assertEqual(code, 0, out)
        self.assertIn("[skip] Python", out)
        self.assertIn("[skip] LibreOffice already installed", out)

    def test_stray_pythonpath_is_reported_and_ignored(self):
        code, out = self.run_script("-DryRun", extra_env={"PYTHONPATH": str(self.t / "nowhere")})
        self.assertEqual(code, 0, out)
        self.assertIn("(PYTHONPATH) can confuse Python", out)
        self.assertIn("Nothing for you to do", out)

    def test_tells_people_not_to_close_the_window_and_shows_time(self):
        code, out = self.run_script("-DryRun")
        self.assertIn("do not close this window", out.lower())
        self.assertRegex(out, r"Step 1 of 5 - .*time so far \d+:\d\d")
        self.put("python.cmd", BROKEN_PIP)
        code, out = self.run_script(path=self.stub_path())
        self.assertIn("Working. This can take a few minutes", out)

    def test_failed_step_gives_next_action_and_a_log(self):
        self.put("python.cmd", BROKEN_PIP)
        code, out = self.run_script(path=self.stub_path())
        self.assertEqual(code, 1, out)
        self.assertIn("Could not set up the helper tool", out)
        self.assertIn("What to do next", out)
        self.assertIn("cannot read", out)                   # the Access is denied hint
        self.assertNotIn("Traceback", out)
        self.assertIn("Access is denied", (self.home / "install-log.txt").read_text(encoding="utf-8"))

    def test_opened_from_inside_a_zip(self):
        d = self.t / "pkg.zip" / "bootstrap"
        d.mkdir(parents=True)
        shutil.copy(SCRIPT, d / "install.ps1")
        code, out = self.run_script("-DryRun", script=d / "install.ps1")
        self.assertEqual(code, 1, out)
        self.assertIn("inside the zip", out)

    def test_wrong_folder_says_to_unzip(self):
        empty = self.t / "empty"
        empty.mkdir()
        code, out = self.run_script("-DryRun", "-Source", str(empty))
        self.assertEqual(code, 1, out)
        self.assertIn("does not look like the Command Center folder", out)

    def test_start_never_waits_in_a_window_when_the_background_task_is_refused(self):
        text = SCRIPT.read_text(encoding="ascii")
        self.assertIn("start --reinstall --no-foreground", text)
        self.assertIn("could not be started in the background", text)

    def test_script_is_plain_ascii(self):
        # Windows PowerShell 5.1 reads a script without a BOM as ANSI, so any other character can break it.
        SCRIPT.read_bytes().decode("ascii")

    def test_launcher_runs_the_script_and_pauses(self):
        text = (ROOT / "Install Command Center.cmd").read_text(encoding="ascii")
        self.assertIn("-ExecutionPolicy Bypass", text)
        self.assertIn("bootstrap\\install.ps1", text)
        self.assertIn("pause", text)


if __name__ == "__main__":
    unittest.main()
