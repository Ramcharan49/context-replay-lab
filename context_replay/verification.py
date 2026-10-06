"""Deterministic requirement/evidence checks, not an LLM or VeriHarness replica.

The task defines the requirement universe independently of candidate answers.
Evidence is a caller-supplied, trusted synthetic snapshot, not world attestation.
Missing claim keys denote omission; null, false, zero, and empty strings do not.
"""

from .core import canonical, digest, integer, keys, parse, require, string


def _identifier(value, label):
    string(value, label)
    require(bool(value.strip()), f"{label}: blank identifier")


def _scalar(value, label):
    require(type(value) in (str, int, float, bool, type(None)),
            f"{label}: expected a JSON scalar")


def _rows(value, label, *, nonempty=True):
    require(type(value) is list and (bool(value) or not nonempty),
            f"{label}: expected {'nonempty ' if nonempty else ''}list")


def _version(value, label):
    integer(value, label)
    require(value == 1, f"{label}: unsupported schema version")


def _validate(task, evidence, pool):
    """Validate all inputs before producing any verdicts."""
    keys(task, {"schema_version", "task_id", "requirements"}, "task")
    _version(task["schema_version"], "task schema")
    _identifier(task["task_id"], "task_id")
    _rows(task["requirements"], "requirements")
    requirement_ids = set()
    for row in task["requirements"]:
        keys(row, {"id", "description", "source_id", "field"}, "requirement")
        for field in ("id", "description", "source_id", "field"):
            _identifier(row[field], f"requirement {field}")
        require(row["id"] not in requirement_ids, "duplicate requirement id")
        requirement_ids.add(row["id"])

    keys(evidence, {"schema_version", "sources"}, "evidence")
    _version(evidence["schema_version"], "evidence schema")
    _rows(evidence["sources"], "sources", nonempty=False)
    source_ids = set()
    for source in evidence["sources"]:
        keys(source, {"source_id", "fields"}, "source")
        _identifier(source["source_id"], "source_id")
        require(source["source_id"] not in source_ids, "duplicate source id")
        source_ids.add(source["source_id"])
        require(type(source["fields"]) is dict, "source fields: expected object")
        for name, value in source["fields"].items():
            _identifier(name, "source field")
            _scalar(value, "source value")

    keys(pool, {"schema_version", "candidates"}, "candidate pool")
    _version(pool["schema_version"], "pool schema")
    _rows(pool["candidates"], "candidates")
    candidate_ids = set()
    for candidate in pool["candidates"]:
        keys(candidate, {"candidate_id", "claims"}, "candidate")
        _identifier(candidate["candidate_id"], "candidate_id")
        require(candidate["candidate_id"] not in candidate_ids,
                "duplicate candidate id")
        candidate_ids.add(candidate["candidate_id"])
        require(type(candidate["claims"]) is dict, "claims: expected object")
        require(set(candidate["claims"]) <= requirement_ids,
                "claim references unknown requirement")
        for value in candidate["claims"].values():
            _scalar(value, "claim value")


def verify(task_bytes: bytes, evidence_bytes: bytes, candidates_bytes: bytes):
    """Audit every declared requirement; return evidence-linked per-claim verdicts.

    Equality uses canonical JSON bytes, so false, 0, and 0.0 are distinct.
    Omitted claims fail coverage even if their source is unavailable. A present
    claim with unavailable evidence stays unresolved and cannot be supported.
    No candidate, requirement, or source is edited by this function.
    """
    task, evidence, pool = map(parse, (task_bytes, evidence_bytes, candidates_bytes))
    _validate(task, evidence, pool)
    candidates = pool["candidates"]
    sources = {source["source_id"]: source["fields"] for source in evidence["sources"]}
    checks = []
    observed_agreements = []
    observed_ids = []
    for requirement in task["requirements"]:
        requirement_id = requirement["id"]
        claims = [candidate["claims"] for candidate in candidates]
        present = [requirement_id in claim for claim in claims]
        # Tag absence separately; no valid JSON value is used as its sentinel.
        signatures = {(True, canonical(claim[requirement_id])) if exists else (False, b"")
                      for exists, claim in zip(present, claims)}
        if not any(present):
            agreement = "shared_omission"
        elif len(signatures) == 1:
            agreement = "unanimous_value"
        else:
            agreement = "disputed"
        if any(present):
            observed_ids.append(requirement_id)
            observed_agreements.append(agreement == "unanimous_value")

        source_id, field = requirement["source_id"], requirement["field"]
        support = {"source_id": source_id, "field": field}
        if source_id not in sources:
            support["status"] = "missing_source"
        elif field not in sources[source_id]:
            support["status"] = "missing_field"
        else:
            support.update(status="available", value=sources[source_id][field])
        verdicts = []
        for candidate, exists in zip(candidates, present):
            row = {"candidate_id": candidate["candidate_id"], "present": exists}
            if not exists:
                row["verdict"] = "omitted"
            else:
                value = candidate["claims"][requirement_id]
                row["value"] = value
                if support["status"] != "available":
                    row["verdict"] = "unresolved"
                elif canonical(value) == canonical(support["value"]):
                    row["verdict"] = "supported"
                else:
                    row["verdict"] = "contradicted"
            verdicts.append(row)
        checks.append({"requirement_id": requirement_id,
                       "description": requirement["description"],
                       "agreement": agreement, "evidence": support,
                       "candidate_verdicts": verdicts})

    summaries = []
    for index, candidate in enumerate(candidates):
        groups = {verdict: [check["requirement_id"] for check in checks
                           if check["candidate_verdicts"][index]["verdict"] == verdict]
                  for verdict in ("supported", "omitted", "contradicted", "unresolved")}
        summaries.append({"candidate_id": candidate["candidate_id"],
                          "all_requirements_supported": len(groups["supported"]) == len(checks),
                          **groups})
    return {
        "report_schema_version": 1,
        "task_id": task["task_id"],
        "method": "declared requirements and exact scalar equality; no model inference",
        "input_sha256": {"task": digest(task_bytes), "evidence": digest(evidence_bytes),
                         "candidates": digest(candidates_bytes)},
        "observed_claim_agreement": {
            "requirement_ids": observed_ids,
            "all_observed_claims_unanimous": bool(observed_ids) and all(observed_agreements),
            "checks_completeness": False,
        },
        "shared_omission_ids": [check["requirement_id"] for check in checks
                                if check["agreement"] == "shared_omission"],
        "checks": checks,
        "candidates": summaries,
        "supported_candidate_ids": [row["candidate_id"] for row in summaries
                                    if row["all_requirements_supported"]],
    }
