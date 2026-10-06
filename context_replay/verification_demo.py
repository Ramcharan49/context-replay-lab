"""Five synthetic controls for requirement-first candidate verification."""

from pathlib import Path

from .verification import verify


def run_verification_demo(directory):
    directory = Path(directory)
    task = (directory / "task.json").read_bytes()
    evidence = (directory / "evidence.json").read_bytes()
    cases = {}
    for name in ("shared_omission", "complete", "shared_wrong", "disagreement", "missing_evidence"):
        case_evidence = ((directory / "missing_evidence.json").read_bytes()
                         if name == "missing_evidence" else evidence)
        pool_name = "complete" if name == "missing_evidence" else name
        cases[name] = verify(task, case_evidence,
                             (directory / f"{pool_name}.candidates.json").read_bytes())
    all_ids = ["candidate-1", "candidate-2", "candidate-3"]
    expected = {
        # Observed-claim unanimity, shared omissions, supported candidates.
        "shared_omission": (True, ["restock_date"], []),
        "complete": (True, [], all_ids),
        "shared_wrong": (True, [], []),
        "disagreement": (False, [], ["candidate-3"]),
        "missing_evidence": (True, [], []),
    }
    controls = {name: (case["observed_claim_agreement"]["all_observed_claims_unanimous"],
                       case["shared_omission_ids"], case["supported_candidate_ids"]) == expected[name]
                for name, case in cases.items()}
    controls["missing_evidence_stays_unresolved"] = all(
        row["unresolved"] == ["restock_date"] for row in cases["missing_evidence"]["candidates"])
    controls["shared_wrong_is_contradicted"] = all(
        row["contradicted"] == ["restock_date"] for row in cases["shared_wrong"]["candidates"])
    return {"experiment": "synthetic shared-omission verification",
            "all_controls_passed": all(controls.values()), "control_checks": controls,
            "cases": cases}
