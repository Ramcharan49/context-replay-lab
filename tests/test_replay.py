"""Executable invariants for the lab; no network, packages, or model calls."""

from copy import deepcopy
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from context_replay import adapter
from context_replay.core import (ValidationError, append_event, canonical, digest,
                                 fingerprint, make_proposal, parse, read_events, replay)
from context_replay.demo import run_demo, whole, write_fixture


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "examples"


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.events = (FIXTURES / "events.jsonl").read_bytes()
        self.baseline = (FIXTURES / "baseline.json").read_bytes()
        self.proposal = (FIXTURES / "positive_restore.proposal.json").read_bytes()
        self.event_rows = read_events(self.events)

    def run_case(self, name):
        return replay(self.events, self.baseline, (FIXTURES / f"{name}.proposal.json").read_bytes())

    def reject(self, *, events=None, baseline=None, proposal=None, match=None):
        with patch("context_replay.adapter.decide") as decide:
            with self.assertRaisesRegex(ValidationError, match or ".+"):
                replay(self.events if events is None else events,
                       self.baseline if baseline is None else baseline,
                       self.proposal if proposal is None else proposal)
            decide.assert_not_called()

    def changed_proposal(self, change):
        data = parse(self.proposal)
        change(data)
        return canonical(data)

    def test_valid_restore_changes_scripted_action(self):
        report = self.run_case("positive_restore")
        self.assertEqual(report["original"]["action"], "request_inventory_check")
        self.assertEqual(report["replay"]["action"], "report_unavailable")
        self.assertTrue(report["action_changed"])
        self.assertEqual(report["interventions"][0]["added_content_bytes"], 43)
        self.assertIn("inventory_status=unavailable", report["snapshot_diff"])

    def test_logged_is_not_visible(self):
        report = self.run_case("positive_restore")
        evidence = report["evidence_visibility"][1]
        self.assertTrue(evidence["logged_before_decision"])
        self.assertEqual(evidence["visible_original_content_bytes"], 0)
        self.assertEqual(evidence["visible_replay_content_bytes"], 43)
        future = report["evidence_visibility"][4]
        self.assertFalse(future["logged_before_decision"])
        self.assertEqual(future["visible_replay_content_bytes"], 0)

    def test_negative_irrelevant_evidence_does_not_change_action(self):
        report = self.run_case("negative_irrelevant")
        self.assertFalse(report["action_changed"])
        self.assertNotEqual(report["original"]["visible_input_sha256"],
                            report["replay"]["visible_input_sha256"])

    def test_already_visible_restoration_is_exact_noop(self):
        report = self.run_case("negative_noop_visible")
        self.assertEqual(report["interventions"][0]["effect"], "no_op_already_visible")
        self.assertEqual(report["original"], report["replay"])
        self.assertEqual(report["snapshot_diff"], "")

    def test_unchanged_control(self):
        report = self.run_case("negative_unchanged")
        self.assertEqual(report["original"], report["replay"])
        self.assertEqual(report["interventions"], [])

    def test_repeat_determinism(self):
        first = canonical(replay(self.events, self.baseline, self.proposal))
        for _ in range(4):
            self.assertEqual(first, canonical(replay(self.events, self.baseline, self.proposal)))

    def test_cli_determinism_and_checked_report(self):
        command = [sys.executable, "-m", "context_replay", "demo"]
        first = subprocess.check_output(command, cwd=ROOT)
        second = subprocess.check_output(command, cwd=ROOT)
        self.assertEqual(first, second)
        self.assertEqual(first, (ROOT / "reports" / "demo.json").read_bytes())

    def test_original_files_and_inputs_unchanged(self):
        paths = sorted(FIXTURES.iterdir())
        before = {path: path.read_bytes() for path in paths}
        inputs_before = (self.events, self.baseline, self.proposal)
        run_demo(FIXTURES)
        self.reject(proposal=b"{}")
        self.assertEqual(before, {path: path.read_bytes() for path in paths})
        self.assertEqual(inputs_before, (self.events, self.baseline, self.proposal))

    def test_adapter_only_receives_exact_visible_bytes(self):
        original_decide = adapter.decide
        with patch("context_replay.adapter.decide", wraps=original_decide) as decide:
            report = replay(self.events, self.baseline, self.proposal)
        self.assertEqual(decide.call_count, 2)
        for call, condition in zip(decide.call_args_list, ("original", "replay")):
            actual = call.args[0]
            self.assertEqual(actual, report[condition]["visible_input_utf8"].encode("utf-8"))
            self.assertEqual(digest(actual), report[condition]["visible_input_sha256"])
            self.assertEqual(set(parse(actual)), {"messages", "tools"})
            self.assertNotIn(b"after the decision", actual)
            self.assertNotIn(b"inventory_count", actual)

    def test_frozen_hashes_identical_across_conditions(self):
        positive = self.run_case("positive_restore")
        irrelevant = self.run_case("negative_irrelevant")
        self.assertEqual(positive["frozen_hashes"], irrelevant["frozen_hashes"])
        self.assertEqual(set(positive["frozen_hashes"]),
                         {"prefix_sha256", "environment_sha256", "tools_sha256", "adapter_sha256"})

    def test_reject_each_frozen_state_confound_before_adapter(self):
        mutations = [
            lambda x: x.update(prefix_sha256="1" * 64),
            lambda x: x["environment"].update(inventory_count=1),
            lambda x: x["tools"][0].update(version="2"),
            lambda x: x["adapter"].update(version="2"),
            lambda x: x.update(restore_budget_bytes=999),
        ]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                proposal = self.changed_proposal(lambda p: mutate(p["frozen"]))
                self.reject(proposal=proposal, match="frozen")

    def test_boolean_and_float_cannot_alias_frozen_integer(self):
        for value in (False, 0.0):
            with self.subTest(value=value):
                proposal = self.changed_proposal(
                    lambda p: p["frozen"]["environment"].update(inventory_count=value))
                self.reject(proposal=proposal, match="frozen")

    def test_original_baseline_raw_hash_is_anchored(self):
        # Even whitespace changes to the original require a new explicit capture anchor.
        self.reject(baseline=self.baseline + b" ", match="original baseline changed")

    def test_adapter_source_identity_change_is_rejected(self):
        with patch("context_replay.adapter.identity", return_value={"name": "different-adapter"}):
            self.reject(match="adapter implementation")

    def test_prefix_modification_with_recomputed_chain_is_rejected(self):
        rows = deepcopy(self.event_rows)
        rows[1]["text"] = "inventory_status=available"
        previous = "0" * 64
        for row in rows:
            row["previous_hash"] = previous
            row["event_hash"] = fingerprint({k: v for k, v in row.items() if k != "event_hash"})
            previous = row["event_hash"]
        self.reject(events=b"".join(canonical(row) + b"\n" for row in rows), match="prefix changed")

    def test_broken_chain_is_rejected(self):
        rows = deepcopy(self.event_rows)
        rows[1]["previous_hash"] = "9" * 64
        self.reject(events=b"".join(canonical(row) + b"\n" for row in rows), match="hash chain")

    def test_unhashed_event_change_is_rejected(self):
        self.reject(events=self.events.replace(b"unavailable", b"unverifiable"), match="content hash")

    def test_future_evidence_in_snapshot_is_rejected(self):
        def mutate(proposal):
            proposal["snapshot"]["messages"][-1]["content"] = self.event_rows[4]["text"]
            proposal["snapshot"]["provenance"][-1] = whole(self.event_rows[4])
            proposal["interventions"][0]["source"] = whole(self.event_rows[4])
        self.reject(proposal=self.changed_proposal(mutate), match="future evidence")

    def test_future_declaration_is_rejected_even_without_visible_edit(self):
        proposal = self.changed_proposal(
            lambda p: p["interventions"][0].update(source=whole(self.event_rows[4])))
        self.reject(proposal=proposal, match="future evidence")

    def test_undeclared_restore_is_rejected(self):
        self.reject(proposal=self.changed_proposal(lambda p: p.update(interventions=[])),
                    match="undeclared context edit")

    def test_reordering_and_deleting_original_messages_are_rejected(self):
        for operation in ("reverse", "pop"):
            def mutate(proposal):
                getattr(proposal["snapshot"]["messages"], operation)()
                getattr(proposal["snapshot"]["provenance"], operation)()
            self.reject(proposal=self.changed_proposal(mutate), match="undeclared context edit")

    def test_fabricated_text_and_role_are_rejected(self):
        for field, value in (("content", "inventory_status=available"), ("role", "system")):
            proposal = self.changed_proposal(lambda p: p["snapshot"]["messages"][-1].update({field: value}))
            self.reject(proposal=proposal, match="does not match source")

    def test_missing_snapshot_or_provenance_is_rejected(self):
        for mutate in (lambda p: p.pop("snapshot"),
                       lambda p: p["snapshot"].pop("provenance"),
                       lambda p: p["snapshot"]["provenance"].pop()):
            self.reject(proposal=self.changed_proposal(mutate), match="fields|provenance")

    def test_invalid_provenance_references_and_offsets(self):
        for update in ({"event_id": "unknown"}, {"start": -1}, {"start": True},
                       {"end": 100000}, {"end": 0}, {"start": 10, "end": 3}):
            with self.subTest(update=update):
                proposal = self.changed_proposal(lambda p: p["snapshot"]["provenance"][-1].update(update))
                self.reject(proposal=proposal)

    def test_utf8_offsets_are_bytes_and_cannot_split_character(self):
        event = self.event_rows[2]
        raw = event["text"].encode("utf-8")
        start = raw.index("é".encode("utf-8"))
        good = {"op": "restore", "source": {"event_id": event["event_id"], "start": start, "end": start + 2}}
        proposal = make_proposal(self.events, self.baseline, [good])
        report = replay(self.events, self.baseline, proposal)
        self.assertEqual(report["replay"]["snapshot"]["messages"][-1]["content"], "é")
        self.assertEqual(report["interventions"][0]["added_content_bytes"], 2)
        bad = deepcopy(good)
        bad["source"]["end"] = start + 1
        with self.assertRaisesRegex(ValidationError, "splits a UTF-8"):
            make_proposal(self.events, self.baseline, [bad])

    def test_partial_span_report_does_not_claim_full_event_visibility(self):
        source = whole(self.event_rows[1])
        source["end"] = 14
        proposal = make_proposal(self.events, self.baseline, [{"op": "restore", "source": source}])
        report = replay(self.events, self.baseline, proposal)
        self.assertEqual(report["evidence_visibility"][1]["visible_replay_content_bytes"], 14)
        self.assertFalse(report["action_changed"])

    def test_duplicate_restoration_is_rejected(self):
        proposal = self.changed_proposal(lambda p: p["interventions"].append(deepcopy(p["interventions"][0])))
        self.reject(proposal=proposal, match="duplicate restoration")

    def test_budget_is_enforced(self):
        baseline = parse(self.baseline)
        baseline["frozen"]["restore_budget_bytes"] = 1
        baseline_bytes = canonical(baseline)
        proposal = parse(self.proposal)
        proposal["baseline_sha256"] = digest(baseline_bytes)
        proposal["frozen"] = baseline["frozen"]
        self.reject(baseline=baseline_bytes, proposal=canonical(proposal), match="exceeds byte budget")

    def test_strict_json_and_schema_rejections(self):
        invalid = [b"", b"null", b"[]", b"{", b"\xff", b'{"x":1,"x":2}',
                   b'{"x":NaN}', b'{"x":Infinity}', b'{"x":"\\ud800"}']
        for raw in invalid:
            with self.subTest(raw=raw):
                self.reject(proposal=raw)
        self.reject(proposal=self.changed_proposal(lambda p: p.update(unrecognized="field")))

    def test_oversized_integer_and_nesting_fail_closed(self):
        self.reject(proposal=b'{"x":' + b"9" * 5000 + b"}")
        self.reject(proposal=b"[" * 2000 + b"0" + b"]" * 2000)

    def test_bad_ledger_shape_sequence_and_duplicate_ids(self):
        for raw in (b"", b"\n", b"{}\n", b"[]\n"):
            self.reject(events=raw)
        for key, value in (("seq", 99), ("event_id", "e0000"), ("seq", True)):
            rows = deepcopy(self.event_rows)
            rows[1][key] = value
            self.reject(events=b"".join(canonical(row) + b"\n" for row in rows))

    def test_carriage_return_whitespace_inside_jsonl_row_is_preserved(self):
        events = self.events.replace(b',', b',\r', 1)
        self.assertEqual(read_events(events), self.event_rows)
        report = replay(events, self.baseline, self.proposal)
        self.assertEqual(report["replay"]["action"], "report_unavailable")
        self.assertEqual(report["originals"]["events_file_sha256"], digest(events))
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "events.jsonl"
            path.write_bytes(events)
            append_event(path, kind="observation", role="tool", text="Later observation.")
            self.assertTrue(path.read_bytes().startswith(events))
            self.assertEqual(len(read_events(path.read_bytes())), len(self.event_rows) + 1)

    def test_bare_carriage_returns_cannot_frame_ledger_records(self):
        self.reject(events=self.events.replace(b"\n", b"\r"), match="malformed UTF-8 JSON")

    def test_ledger_accepts_lf_crlf_and_optional_final_newline(self):
        for events in (self.events, self.events.rstrip(b"\n"),
                       self.events.replace(b"\n", b"\r\n")):
            with self.subTest(events=events):
                self.assertEqual(read_events(events), self.event_rows)

    def test_ledger_rejects_blank_records_including_extra_final_newline(self):
        for events in (b"\n" + self.events, self.events + b"\n",
                       self.events.replace(b"\n", b"\n \r\n", 1)):
            with self.subTest(events=events):
                self.reject(events=events, match="blank event line")

    def test_append_preserves_original_prefix_and_replay(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "events.jsonl"
            path.write_bytes(self.events)
            append_event(path, kind="observation", role="tool", text="Another later event.")
            self.assertTrue(path.read_bytes().startswith(self.events))
            report = replay(path.read_bytes(), self.baseline, self.proposal)
            self.assertEqual(report["replay"]["action"], "report_unavailable")
            self.assertFalse(report["evidence_visibility"][-1]["logged_before_decision"])

    def test_invalid_append_does_not_write(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "events.jsonl"
            path.write_bytes(self.events)
            with self.assertRaises(ValidationError):
                append_event(path, kind="not-a-kind", role="tool", text="invalid")
            self.assertEqual(path.read_bytes(), self.events)
            path.write_bytes(self.events.rstrip(b"\n"))
            with self.assertRaisesRegex(ValidationError, "final newline"):
                append_event(path, kind="observation", role="tool", text="invalid framing")

    def test_fixture_creation_is_deterministic_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "capture"
            write_fixture(destination)
            for path in FIXTURES.iterdir():
                self.assertEqual(path.read_bytes(), (destination / path.name).read_bytes())
            with self.assertRaises(FileExistsError):
                write_fixture(destination)

    def test_cli_refuses_overwrite_and_rejects_invalid_proposal(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "report.json"
            output.write_bytes(b"keep me")
            result = subprocess.run([sys.executable, "-m", "context_replay", "demo", "--out", str(output)],
                                    cwd=ROOT, capture_output=True)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(output.read_bytes(), b"keep me")
            invalid = Path(temp) / "invalid.json"
            invalid.write_text("{}")
            result = subprocess.run([sys.executable, "-m", "context_replay", "replay",
                                     "--events", str(FIXTURES / "events.jsonl"),
                                     "--baseline", str(FIXTURES / "baseline.json"),
                                     "--proposal", str(invalid)], cwd=ROOT, capture_output=True)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, b"")
            self.assertIn(b"Rejected:", result.stderr)


if __name__ == "__main__":
    unittest.main()
