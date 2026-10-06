"""Requirement coverage is independent of agreement between scripted answers."""

from copy import deepcopy
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from context_replay.core import ValidationError, canonical, digest, parse
from context_replay.verification import verify
from context_replay.verification_demo import run_verification_demo


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "experiments" / "shared_omission"


class VerificationTests(unittest.TestCase):
    def setUp(self):
        self.task = (FIXTURES / "task.json").read_bytes()
        self.evidence = (FIXTURES / "evidence.json").read_bytes()
        self.candidates = (FIXTURES / "complete.candidates.json").read_bytes()

    def report(self, case="complete", *, task=None, evidence=None, candidates=None):
        return verify(self.task if task is None else canonical(task),
                      self.evidence if evidence is None else canonical(evidence),
                      (FIXTURES / f"{case}.candidates.json").read_bytes()
                      if candidates is None else canonical(candidates))

    def reject(self, index, value):
        args = [self.task, self.evidence, self.candidates]
        args[index] = canonical(value)
        with self.assertRaises(ValidationError):
            verify(*args)

    def test_shared_omission_defeats_observed_claim_agreement(self):
        report = self.report("shared_omission")
        self.assertTrue(report["observed_claim_agreement"]["all_observed_claims_unanimous"])
        self.assertFalse(report["observed_claim_agreement"]["checks_completeness"])
        self.assertEqual(report["observed_claim_agreement"]["requirement_ids"], ["availability"])
        self.assertEqual(report["shared_omission_ids"], ["restock_date"])
        self.assertEqual(report["supported_candidate_ids"], [])
        for candidate in report["candidates"]:
            self.assertEqual(candidate["omitted"], ["restock_date"])
            self.assertEqual(candidate["supported"], ["availability"])
        missing = report["checks"][1]
        self.assertEqual(missing["agreement"], "shared_omission")
        self.assertEqual(missing["evidence"], {"source_id": "inventory-snapshot",
                         "field": "restock_date", "status": "available", "value": "2030-01-15"})

    def test_complete_control_supports_every_candidate(self):
        report = self.report()
        self.assertEqual(report["shared_omission_ids"], [])
        self.assertEqual(report["supported_candidate_ids"],
                         ["candidate-1", "candidate-2", "candidate-3"])

    def test_shared_wrong_value_is_not_supported_by_unanimity(self):
        report = self.report("shared_wrong")
        self.assertTrue(report["observed_claim_agreement"]["all_observed_claims_unanimous"])
        self.assertEqual(report["checks"][1]["agreement"], "unanimous_value")
        self.assertEqual(report["supported_candidate_ids"], [])
        self.assertTrue(all(row["contradicted"] == ["restock_date"] for row in report["candidates"]))

    def test_evidence_supports_minority_in_disagreement(self):
        report = self.report("disagreement")
        self.assertFalse(report["observed_claim_agreement"]["all_observed_claims_unanimous"])
        self.assertEqual(report["checks"][1]["agreement"], "disputed")
        self.assertEqual(report["supported_candidate_ids"], ["candidate-3"])

    def test_missing_source_and_field_stay_unresolved(self):
        for sources, expected in (([], "missing_source"),
                                  ([{"source_id": "inventory-snapshot", "fields": {}}], "missing_field")):
            with self.subTest(expected=expected):
                report = self.report(evidence={"schema_version": 1, "sources": sources})
                self.assertEqual(report["supported_candidate_ids"], [])
                self.assertTrue(all(check["evidence"]["status"] == expected for check in report["checks"]))
                for row in report["candidates"]:
                    self.assertEqual(row["unresolved"], ["availability", "restock_date"])
                    self.assertEqual(row["contradicted"], [])

    def test_missing_evidence_does_not_erase_known_omission(self):
        report = self.report("shared_omission", evidence={"schema_version": 1, "sources": []})
        for row in report["candidates"]:
            self.assertEqual(row["omitted"], ["restock_date"])
            self.assertEqual(row["unresolved"], ["availability"])

    def test_partial_omission_is_disputed_not_shared(self):
        pool = parse(self.candidates)
        del pool["candidates"][0]["claims"]["restock_date"]
        report = self.report(candidates=pool)
        self.assertEqual(report["checks"][1]["agreement"], "disputed")
        self.assertEqual(report["shared_omission_ids"], [])
        self.assertEqual(report["supported_candidate_ids"], ["candidate-2", "candidate-3"])

    def test_all_empty_answers_do_not_vacuously_agree_or_pass(self):
        pool = parse(self.candidates)
        for candidate in pool["candidates"]:
            candidate["claims"] = {}
        report = self.report(candidates=pool)
        self.assertFalse(report["observed_claim_agreement"]["all_observed_claims_unanimous"])
        self.assertEqual(report["supported_candidate_ids"], [])
        self.assertEqual(report["shared_omission_ids"], ["availability", "restock_date"])

    def test_null_false_zero_and_empty_string_are_present_claims(self):
        for value in (None, False, 0, 0.0, ""):
            with self.subTest(value=value):
                evidence, pool = parse(self.evidence), parse(self.candidates)
                evidence["sources"][0]["fields"]["restock_date"] = value
                for row in pool["candidates"]:
                    row["claims"]["restock_date"] = value
                report = self.report(evidence=evidence, candidates=pool)
                self.assertEqual(len(report["supported_candidate_ids"]), 3)
                self.assertEqual(report["shared_omission_ids"], [])
                self.assertTrue(all(row["present"] for row in report["checks"][1]["candidate_verdicts"]))

    def test_scalar_types_cannot_alias_in_verification_or_agreement(self):
        evidence, pool = parse(self.evidence), parse(self.candidates)
        evidence["sources"][0]["fields"]["restock_date"] = False
        for candidate, value in zip(pool["candidates"], (False, 0, 0.0)):
            candidate["claims"]["restock_date"] = value
        report = self.report(evidence=evidence, candidates=pool)
        self.assertEqual(report["checks"][1]["agreement"], "disputed")
        self.assertEqual(report["supported_candidate_ids"], ["candidate-1"])

    def test_claim_null_is_distinct_from_omission(self):
        pool = parse(self.candidates)
        pool["candidates"][0]["claims"]["restock_date"] = None
        del pool["candidates"][1]["claims"]["restock_date"]
        report = self.report(candidates=pool)
        self.assertEqual([row["verdict"] for row in report["checks"][1]["candidate_verdicts"]],
                         ["contradicted", "omitted", "supported"])

    def test_irrelevant_source_data_cannot_fill_a_missing_required_field(self):
        evidence = parse(self.evidence)
        del evidence["sources"][0]["fields"]["restock_date"]
        evidence["sources"][0]["fields"]["unrelated_date"] = "2030-01-15"
        evidence["sources"].append({"source_id": "other-item", "fields": {"restock_date": "2030-01-15"}})
        report = self.report(evidence=evidence)
        self.assertEqual(report["checks"][1]["evidence"]["status"], "missing_field")
        self.assertEqual(report["supported_candidate_ids"], [])

    def test_task_requirements_not_candidate_keys_define_coverage(self):
        task = parse(self.task)
        task["requirements"].append({"id": "warehouse", "description": "Include warehouse.",
                                     "source_id": "inventory-snapshot", "field": "warehouse"})
        report = self.report(task=task)
        self.assertEqual(report["shared_omission_ids"], ["warehouse"])
        self.assertEqual(report["supported_candidate_ids"], [])

    def test_duplicating_wrong_candidates_cannot_outvote_evidence(self):
        pool = parse((FIXTURES / "disagreement.candidates.json").read_bytes())
        for index in range(4, 14):
            row = deepcopy(pool["candidates"][0])
            row["candidate_id"] = f"candidate-{index}"
            pool["candidates"].append(row)
        self.assertEqual(self.report(candidates=pool)["supported_candidate_ids"], ["candidate-3"])

    def test_all_input_shapes_versions_and_unknown_fields_fail_closed(self):
        for index, original in enumerate((self.task, self.evidence, self.candidates)):
            for value in (None, [], 1, {}, "wrong"):
                self.reject(index, value)
            for version in (True, 1.0, 2, -1, "1"):
                value = parse(original)
                value["schema_version"] = version
                self.reject(index, value)
            value = parse(original)
            value["unexpected"] = "field"
            self.reject(index, value)
            value = parse(original)
            del value["schema_version"]
            self.reject(index, value)

    def test_duplicate_ids_and_bad_rows_rejected(self):
        for index, original, field in ((0, self.task, "requirements"),
                                       (1, self.evidence, "sources"),
                                       (2, self.candidates, "candidates")):
            value = parse(original)
            value[field].append(deepcopy(value[field][0]))
            self.reject(index, value)
            for bad in (None, {}, "bad", [None], [{}]):
                value = parse(original)
                value[field] = bad
                self.reject(index, value)
            if field != "sources":
                value = parse(original)
                value[field] = []
                self.reject(index, value)

    def test_identifiers_claim_keys_and_values_validated(self):
        for bad in (None, [], {}, 0, "", " "):
            task = parse(self.task)
            task["requirements"][0]["id"] = bad
            self.reject(0, task)
        pool = parse(self.candidates)
        pool["candidates"][0]["claims"]["unrequested"] = "value"
        self.reject(2, pool)
        for bad in ([], {}, ["not scalar"]):
            evidence, pool = parse(self.evidence), parse(self.candidates)
            evidence["sources"][0]["fields"]["restock_date"] = bad
            pool["candidates"][0]["claims"]["restock_date"] = bad
            self.reject(1, evidence)
            self.reject(2, pool)
        for bad in (None, [], "bad"):
            evidence, pool = parse(self.evidence), parse(self.candidates)
            evidence["sources"][0]["fields"] = bad
            pool["candidates"][0]["claims"] = bad
            self.reject(1, evidence)
            self.reject(2, pool)

    def test_strict_json_and_bytes_at_every_boundary(self):
        for index in range(3):
            for bad in (b"", b"\xff", b'{"x":1,"x":2}', b'{"x":NaN}',
                        b'{"x":1e999}', b'{"x":"\\ud800"}', "{}", bytearray(b"{}")):
                with self.subTest(index=index, bad=bad):
                    args = [self.task, self.evidence, self.candidates]
                    args[index] = bad
                    with self.assertRaises(ValidationError):
                        verify(*args)

    def test_repeat_determinism_raw_hashes_and_immutability(self):
        inputs = [self.task, self.evidence, self.candidates]
        before = {path: path.read_bytes() for path in FIXTURES.glob("*.json")}
        report = verify(*inputs)
        self.assertEqual(report["input_sha256"],
                         dict(zip(("task", "evidence", "candidates"), map(digest, inputs))))
        for _ in range(3):
            self.assertEqual(canonical(report), canonical(verify(*inputs)))
        self.assertEqual(before, {path: path.read_bytes() for path in FIXTURES.glob("*.json")})

    def test_demo_checks_and_checked_report(self):
        report = run_verification_demo(FIXTURES)
        self.assertTrue(report["all_controls_passed"])
        output = subprocess.check_output([sys.executable, "-m", "context_replay", "verify-demo"], cwd=ROOT)
        self.assertEqual(parse(output), report)
        self.assertEqual(output, (ROOT / "reports" / "verification_demo.json").read_bytes())
        self.assertEqual(output, subprocess.check_output(
            [sys.executable, "-m", "context_replay", "verify-demo"], cwd=ROOT))

    def test_cli_exit_codes_and_refusal_to_overwrite(self):
        base = [sys.executable, "-m", "context_replay", "verify", "--task", str(FIXTURES / "task.json"),
                "--evidence", str(FIXTURES / "evidence.json"), "--candidates"]
        for case, code in (("complete", 0), ("shared_omission", 1), ("disagreement", 0)):
            result = subprocess.run(base + [str(FIXTURES / f"{case}.candidates.json")],
                                    cwd=ROOT, capture_output=True)
            self.assertEqual(result.returncode, code)
            self.assertIn("supported_candidate_ids", parse(result.stdout))
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "report.json"
            command = base + [str(FIXTURES / "shared_omission.candidates.json"), "--out", str(output)]
            result = subprocess.run(command, cwd=ROOT, capture_output=True)
            self.assertEqual(result.returncode, 1)
            saved = output.read_bytes()
            result = subprocess.run(command, cwd=ROOT, capture_output=True)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(output.read_bytes(), saved)
            invalid = Path(temp) / "invalid.json"
            invalid.write_text("{}")
            result = subprocess.run(base + [str(invalid)], cwd=ROOT, capture_output=True)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, b"")
            self.assertIn(b"Rejected:", result.stderr)


if __name__ == "__main__":
    unittest.main()
