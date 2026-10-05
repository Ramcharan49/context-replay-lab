"""Original synthetic inventory fixture and positive/negative controls."""

from pathlib import Path

from . import adapter
from .core import append_event, canonical, fingerprint, make_proposal, read_events, replay


def whole(event):
    return {"event_id": event["event_id"], "start": 0,
            "end": len(event["text"].encode("utf-8"))}


def snapshot_for(events):
    return {"messages": [{"role": e["role"], "content": e["text"]} for e in events],
            "provenance": [whole(e) for e in events]}


def write_fixture(directory):
    """Create a fresh fixture directory; never overwrite an earlier capture."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    ledger = directory / "events.jsonl"
    rows = [
        ("instruction", "user", "Check whether synthetic item DEMO-7 is available."),
        ("observation", "tool", "item_id=DEMO-7\ninventory_status=unavailable"),
        ("observation", "tool", "catalog_note=The demonstration shelf is beside the café."),
        ("summary", "assistant", "We checked the catalog. Next, answer the availability question."),
        ("observation", "tool", "item_id=DEMO-7\ninventory_status=available\nThis update arrived after the decision."),
    ]
    for kind, role, text in rows:
        append_event(ledger, kind=kind, role=role, text=text)
    event_bytes = ledger.read_bytes()
    events = read_events(event_bytes)
    baseline = {
        "schema_version": 1, "decision_id": "synthetic-inventory-before-answer", "prefix_length": 4,
        "frozen": {
            "prefix_sha256": fingerprint(events[:4]),
            "environment": {"world": "synthetic-only", "item_id": "DEMO-7", "inventory_count": 0,
                            "revision": "before-later-restock"},
            "tools": [{"name": "lookup_inventory", "version": "1", "input_schema": {
                "type": "object", "properties": {"item_id": {"type": "string"}},
                "required": ["item_id"], "additionalProperties": False}}],
            "adapter": adapter.identity(), "restore_budget_bytes": 256,
        },
        "snapshot": snapshot_for([events[0], events[3]]),
    }
    baseline_bytes = canonical(baseline) + b"\n"
    (directory / "baseline.json").write_bytes(baseline_bytes)
    cases = {"positive_restore": [whole(events[1])],
             "negative_irrelevant": [whole(events[2])],
             "negative_noop_visible": [whole(events[0])],
             "negative_unchanged": []}
    for name, sources in cases.items():
        proposal = make_proposal(event_bytes, baseline_bytes,
                                 [{"op": "restore", "source": source} for source in sources])
        (directory / f"{name}.proposal.json").write_bytes(proposal + b"\n")


def run_demo(directory):
    directory = Path(directory)
    event_bytes = (directory / "events.jsonl").read_bytes()
    baseline_bytes = (directory / "baseline.json").read_bytes()
    cases = {}
    for name in ("positive_restore", "negative_irrelevant", "negative_noop_visible", "negative_unchanged"):
        cases[name] = replay(event_bytes, baseline_bytes,
                             (directory / f"{name}.proposal.json").read_bytes())
    expected = {"positive_restore": ("request_inventory_check", "report_unavailable"),
                "negative_irrelevant": ("request_inventory_check", "request_inventory_check"),
                "negative_noop_visible": ("request_inventory_check", "request_inventory_check"),
                "negative_unchanged": ("request_inventory_check", "request_inventory_check")}
    checks = {name: (case["original"]["action"], case["replay"]["action"]) == expected[name]
              for name, case in cases.items()}
    return {"experiment": "synthetic inventory decision-point context replay",
            "adapter_kind": "scripted; no inference or model quality evidence",
            "control_checks": checks, "all_controls_passed": all(checks.values()), "cases": cases}
