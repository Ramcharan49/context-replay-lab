"""Numeric decoding must not invent agreement or evidence support."""

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from context_replay.core import ValidationError, canonical, parse, replay
from context_replay.verification import verify


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "experiments" / "shared_omission"


class JsonNumberTests(unittest.TestCase):
    def test_lossy_decimal_tokens_are_rejected_in_nested_inputs(self):
        for token in (b"1e-999", b"-1e-999", b"9007199254740993.0",
                      b"0.10000000000000001", b"1.0000000000000001"):
            with self.subTest(token=token):
                with self.assertRaisesRegex(ValidationError, "decimal value"):
                    parse(b'{"nested":[' + token + b"]}")

    def test_overflow_is_rejected(self):
        for token in (b"1e999", b"-1e999"):
            with self.subTest(token=token):
                with self.assertRaises(ValidationError):
                    parse(token)

    def test_lossless_decimal_spellings_normalize_as_floats(self):
        for token, expected in ((b"0.1", b"0.1"), (b"0.1000", b"0.1"),
                                (b"1e0", b"1.0"), (b"-0e-999", b"-0.0"),
                                (b"5e-324", b"5e-324"),
                                (b"1.7976931348623157e308", b"1.7976931348623157e+308")):
            with self.subTest(token=token):
                value = parse(token)
                self.assertIs(type(value), float)
                self.assertEqual(canonical(value), expected)
                self.assertEqual(canonical(parse(canonical(value))), expected)

    def test_large_integers_remain_exact_and_distinct_from_floats(self):
        token = b"9007199254740993"
        self.assertIs(type(parse(token)), int)
        self.assertEqual(canonical(parse(token)), token)
        self.assertNotEqual(canonical(parse(b"1")), canonical(parse(b"1.0")))

    def test_lossy_evidence_or_claim_cannot_produce_supported_verdicts(self):
        task = (FIXTURES / "task.json").read_bytes()
        evidence = (FIXTURES / "evidence.json").read_bytes()
        pool = (FIXTURES / "complete.candidates.json").read_bytes()
        for lossy, rounded in ((b"1e-999", b"0.0"),
                               (b"9007199254740993.0", b"9007199254740992.0")):
            for lossy_index in (1, 2):
                with self.subTest(lossy=lossy, lossy_index=lossy_index):
                    args = [task, evidence.replace(b'"2030-01-15"', rounded),
                            pool.replace(b'"2030-01-15"', rounded)]
                    args[lossy_index] = args[lossy_index].replace(rounded, lossy)
                    with self.assertRaisesRegex(ValidationError, "decimal value"):
                        verify(*args)

    def test_replay_rejects_lossy_frozen_input_before_adapter_calls(self):
        examples = ROOT / "examples"
        baseline = (examples / "baseline.json").read_bytes()
        baseline = baseline.replace(b'"environment":{', b'"environment":{"numeric":1e-999,')
        self.assertIn(b"1e-999", baseline)
        with patch("context_replay.adapter.decide") as decide:
            with self.assertRaisesRegex(ValidationError, "decimal value"):
                replay((examples / "events.jsonl").read_bytes(), baseline,
                       (examples / "positive_restore.proposal.json").read_bytes())
            decide.assert_not_called()

    def test_cli_rejects_lossy_claim_without_emitting_or_saving_report(self):
        with tempfile.TemporaryDirectory() as temp:
            candidates = Path(temp) / "candidates.json"
            candidates.write_bytes((FIXTURES / "complete.candidates.json").read_bytes()
                                   .replace(b'"2030-01-15"', b"1e-999"))
            output = Path(temp) / "report.json"
            result = subprocess.run(
                [sys.executable, "-m", "context_replay", "verify",
                 "--task", str(FIXTURES / "task.json"),
                 "--evidence", str(FIXTURES / "evidence.json"),
                 "--candidates", str(candidates), "--out", str(output)],
                cwd=ROOT, capture_output=True)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, b"")
            self.assertIn(b"decimal value", result.stderr)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
