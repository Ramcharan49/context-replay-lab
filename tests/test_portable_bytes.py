"""Reports and hashed captures retain their bytes across checkout platforms."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from context_replay.core import canonical, parse


ROOT = Path(__file__).resolve().parents[1]


class PortableBytesTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("git"), "Git needed for checkout regression")
    def test_autocrlf_checkout_runs_unchanged_capture(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source"
            source.mkdir()
            for directory in ("context_replay", "examples", "experiments", "reports"):
                shutil.copytree(ROOT / directory, source / directory,
                                ignore=shutil.ignore_patterns("__pycache__"))
            for path in source.rglob("*"):
                if path.is_file():
                    path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n"))
            attributes = ROOT / ".gitattributes"
            if attributes.exists():
                shutil.copyfile(attributes, source / attributes.name)

            def git(*args, cwd=source):
                return subprocess.check_output(["git", *args], cwd=cwd,
                                               stderr=subprocess.STDOUT)

            git("init", "--quiet")
            git("-c", "core.autocrlf=false", "add", ".")
            git("-c", "user.name=Regression Test", "-c",
                "user.email=test@example.invalid", "commit", "--quiet", "-m", "fixture")
            checkout = Path(temp) / "checkout"
            git("-c", "core.autocrlf=true", "clone", "--quiet", str(source), str(checkout))
            for path in ("context_replay/adapter.py", "examples/baseline.json",
                         "examples/events.jsonl", "examples/positive_restore.proposal.json"):
                with self.subTest(path=path):
                    blob = git("show", f"HEAD:{path}")
                    self.assertEqual((checkout / path).read_bytes(), blob)
            for command, report in (("demo", "demo.json"),
                                    ("verify-demo", "verification_demo.json")):
                with self.subTest(command=command):
                    result = subprocess.run([sys.executable, "-m", "context_replay", command],
                                            cwd=checkout, capture_output=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout, git("show", f"HEAD:reports/{report}"))

    def test_stdout_matches_utf8_report_file_with_ascii_locale(self):
        fixtures = ROOT / "experiments" / "shared_omission"
        with tempfile.TemporaryDirectory() as temp:
            task = parse((fixtures / "task.json").read_bytes())
            task["task_id"] = "caf\u00e9-\u03bb"
            task_path = Path(temp) / "task.json"
            task_path.write_bytes(canonical(task))
            command = [sys.executable, "-m", "context_replay", "verify",
                       "--task", str(task_path), "--evidence", str(fixtures / "evidence.json"),
                       "--candidates", str(fixtures / "complete.candidates.json")]
            env = dict(os.environ, PYTHONIOENCODING="ascii")
            output = Path(temp) / "report.json"
            saved = subprocess.run([*command, "--out", str(output)], cwd=ROOT,
                                   env=env, capture_output=True)
            self.assertEqual(saved.returncode, 0, saved.stderr)
            printed = subprocess.run(command, cwd=ROOT, env=env, capture_output=True)
            self.assertEqual(printed.returncode, 0, printed.stderr)
            self.assertEqual(printed.stdout, output.read_bytes())
            self.assertIn("caf\u00e9-\u03bb".encode("utf-8"), printed.stdout)
            self.assertNotIn(b"\r\n", printed.stdout)


if __name__ == "__main__":
    unittest.main()
