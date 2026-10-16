"""bootstrap/install.sh: dry run and argument errors only (a real install needs a macOS or Linux machine)."""
import shutil
import subprocess
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "bootstrap" / "install.sh"


@unittest.skipUnless(shutil.which("sh"), "no POSIX shell")
class InstallSh(unittest.TestCase):
    def run_sh(self, *args):
        return subprocess.run(["sh", str(SCRIPT), *args], capture_output=True, text=True, timeout=60)

    def test_dry_run_changes_nothing_and_lists_steps(self):
        r = self.run_sh("--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        for want in ("WOULD: create a virtual environment", "WOULD: pip install", "WOULD: link cc", "WOULD: run cc setup"):
            self.assertIn(want, r.stdout)

    def test_bad_option_and_bad_source_fail_with_one_line(self):
        for args in (("--bogus",), ("--source",), ("--source", "/nonexistent-folder", "--dry-run")):
            r = self.run_sh(*args)
            self.assertEqual(r.returncode, 1, args)
            self.assertTrue(r.stderr.startswith("ERROR:"), r.stderr)
            self.assertEqual(len(r.stderr.strip().splitlines()), 1)

    def test_syntax(self):
        self.assertEqual(subprocess.run(["sh", "-n", str(SCRIPT)]).returncode, 0)


if __name__ == "__main__":
    unittest.main()
