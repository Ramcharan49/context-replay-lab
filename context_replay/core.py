"""Read-only replay with a validation gate before either adapter invocation.

Public replay inputs are immutable bytes. JSON objects are reconstructed privately
for each call. Hashes are consistency checks relative to a trusted capture, not
signatures or independent verification of the external world.
"""

from copy import deepcopy
from decimal import Decimal, InvalidOperation
from difflib import unified_diff
import hashlib
import json
from pathlib import Path

from . import adapter


class ValidationError(ValueError):
    """The proposed comparison is not a valid context-only intervention."""


def require(condition, message):
    if not condition:
        raise ValidationError(message)


def canonical(value) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as error:
        raise ValidationError("value is not canonical UTF-8 JSON") from error


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fingerprint(value) -> str:
    return digest(canonical(value))


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, f"duplicate JSON key: {key}")
        result[key] = value
    return result


def parse(data: bytes):
    """Decode JSON without changing decimal values during float normalization."""
    require(type(data) is bytes, "input must be immutable bytes")
    try:
        result = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_object,
                            parse_float=_lossless_float,
                            parse_constant=lambda value: _reject_constant(value))
        canonical(result)  # Reject unpaired surrogates, including escaped ones.
        return result
    except ValidationError:
        raise
    except (UnicodeError, ValueError, RecursionError) as error:
        raise ValidationError("malformed UTF-8 JSON") from error


def _reject_constant(value):
    raise ValidationError(f"non-finite JSON number: {value}")


def _lossless_float(token):
    """Keep floats only when their JSON spelling preserves the decimal value.

    Compare decimal spellings, not the exact binary expansion: ordinary 0.1 is
    valid, but 0.10000000000000001 must not silently become that same value.
    """
    value = float(token)
    try:
        original = Decimal(token)
        require(original.is_finite() and Decimal(str(value)) == original,
                "JSON number cannot preserve its decimal value as a float")
    except InvalidOperation as error:
        raise ValidationError("invalid JSON decimal value") from error
    return value


def keys(value, expected, label):
    require(type(value) is dict and set(value) == set(expected),
            f"{label}: missing or unknown fields")


def integer(value, label, minimum=0):
    require(type(value) is int and value >= minimum, f"{label}: invalid integer")


def string(value, label):
    require(type(value) is str and bool(value), f"{label}: expected nonempty string")


def hash_string(value, label):
    require(type(value) is str and len(value) == 64
            and all(c in "0123456789abcdef" for c in value), f"{label}: invalid SHA-256")


def read_events(data: bytes):
    require(type(data) is bytes and bool(data), "event ledger is missing or empty")
    events = []
    previous_hash = "0" * 64
    seen = set()
    for sequence, line in enumerate(data.splitlines()):
        require(bool(line.strip()), "blank event line")
        event = parse(line)
        keys(event, {"event_id", "seq", "kind", "role", "text", "previous_hash",
                     "event_hash"}, "event")
        string(event["event_id"], "event_id")
        require(event["event_id"] not in seen, "duplicate event_id")
        integer(event["seq"], "event seq")
        require(event["seq"] == sequence, "event sequence is not contiguous")
        require(event["kind"] in ("instruction", "observation", "summary"), "invalid event kind")
        require(event["role"] in ("system", "user", "assistant", "tool"), "invalid event role")
        string(event["text"], "event text")
        require(event["previous_hash"] == previous_hash, "broken event hash chain")
        body = {key: value for key, value in event.items() if key != "event_hash"}
        require(event["event_hash"] == fingerprint(body), "event content hash mismatch")
        previous_hash = event["event_hash"]
        seen.add(event["event_id"])
        events.append(event)
    return events


def append_event(path: Path, *, kind: str, role: str, text: str):
    """Append one synthetic event without rewriting earlier bytes.

    Single-writer teaching helper; NOT a concurrent or crash-atomic event store.
    Existing ledgers must end with a newline so appending preserves JSONL framing.
    """
    path = Path(path)
    old = path.read_bytes() if path.exists() else b""
    require(not old or old.endswith(b"\n"), "existing ledger needs a final newline")
    events = read_events(old) if old else []
    body = {"event_id": f"e{len(events):04d}", "seq": len(events), "kind": kind,
            "role": role, "text": text,
            "previous_hash": events[-1]["event_hash"] if events else "0" * 64}
    event = dict(body, event_hash=fingerprint(body))
    encoded = canonical(event) + b"\n"
    read_events(old + encoded)  # No writes on validation failure.
    with path.open("ab") as handle:
        handle.write(encoded)
    return event


def source_text(source, events, prefix_length):
    keys(source, {"event_id", "start", "end"}, "source provenance")
    string(source["event_id"], "source event_id")
    integer(source["start"], "span start")
    integer(source["end"], "span end", 1)
    event = next((e for e in events if e["event_id"] == source["event_id"]), None)
    require(event is not None, "unknown source event")
    require(event["seq"] < prefix_length, "future evidence is forbidden")
    raw = event["text"].encode("utf-8")
    require(source["start"] < source["end"] <= len(raw), "span is outside event text")
    try:
        content = raw[source["start"]:source["end"]].decode("utf-8")
    except UnicodeError as error:
        raise ValidationError("span splits a UTF-8 character") from error
    return event, content


def validate_snapshot(snapshot, events, prefix_length):
    keys(snapshot, {"messages", "provenance"}, "snapshot")
    require(type(snapshot["messages"]) is list and bool(snapshot["messages"]),
            "snapshot messages must be a nonempty list")
    require(type(snapshot["provenance"]) is list
            and len(snapshot["messages"]) == len(snapshot["provenance"]),
            "snapshot requires provenance for every message")
    for message, source in zip(snapshot["messages"], snapshot["provenance"]):
        keys(message, {"role", "content"}, "message")
        event, content = source_text(source, events, prefix_length)
        require(message == {"role": event["role"], "content": content},
                "snapshot content or role does not match source provenance")


def validate_baseline(event_bytes: bytes, baseline_bytes: bytes):
    events, baseline = read_events(event_bytes), parse(baseline_bytes)
    keys(baseline, {"schema_version", "decision_id", "prefix_length", "frozen", "snapshot"},
         "baseline")
    require(type(baseline["schema_version"]) is int and baseline["schema_version"] == 1,
            "unsupported schema version")
    string(baseline["decision_id"], "decision_id")
    integer(baseline["prefix_length"], "prefix_length", 1)
    require(baseline["prefix_length"] <= len(events), "prefix exceeds event ledger")
    frozen = baseline["frozen"]
    keys(frozen, {"prefix_sha256", "environment", "tools", "adapter", "restore_budget_bytes"},
         "frozen state")
    hash_string(frozen["prefix_sha256"], "prefix hash")
    require(frozen["prefix_sha256"] == fingerprint(events[:baseline["prefix_length"]]),
            "execution prefix changed")
    require(type(frozen["environment"]) is dict and bool(frozen["environment"]),
            "environment snapshot missing")
    require(type(frozen["tools"]) is list and bool(frozen["tools"]), "tools snapshot missing")
    for tool in frozen["tools"]:
        keys(tool, {"name", "version", "input_schema"}, "tool")
        string(tool["name"], "tool name")
        string(tool["version"], "tool version")
        require(type(tool["input_schema"]) is dict, "invalid tool input_schema")
    require(canonical(frozen["adapter"]) == canonical(adapter.identity()),
            "adapter implementation or settings changed")
    integer(frozen["restore_budget_bytes"], "restore budget")
    validate_snapshot(baseline["snapshot"], events, baseline["prefix_length"])
    return events, baseline


def span_visible(source, snapshot):
    """True only when one visible message contains the entire exact source span."""
    return any(other["event_id"] == source["event_id"]
               and other["start"] <= source["start"] and other["end"] >= source["end"]
               for other in snapshot["provenance"])


def restore_snapshot(baseline, events, interventions):
    require(type(interventions) is list, "interventions must be a list")
    result = deepcopy(baseline["snapshot"])
    seen, added_bytes, changes = set(), 0, []
    for intervention in interventions:
        keys(intervention, {"op", "source"}, "intervention")
        require(intervention["op"] == "restore", "only declared restore operations are allowed")
        source = intervention["source"]
        event, content = source_text(source, events, baseline["prefix_length"])
        signature = canonical(source)
        require(signature not in seen, "duplicate restoration declaration")
        seen.add(signature)
        already_visible = span_visible(source, result)
        changes.append({"source": deepcopy(source), "logged_before_decision": True,
                        "visible_in_original": span_visible(source, baseline["snapshot"]),
                        "effect": "no_op_already_visible" if already_visible else "appended",
                        "added_content_bytes": 0 if already_visible else len(content.encode("utf-8"))})
        if not already_visible:
            result["messages"].append({"role": event["role"], "content": content})
            result["provenance"].append(deepcopy(source))
            added_bytes += len(content.encode("utf-8"))
    require(added_bytes <= baseline["frozen"]["restore_budget_bytes"], "restoration exceeds byte budget")
    return result, changes


def make_proposal(event_bytes: bytes, baseline_bytes: bytes, interventions) -> bytes:
    events, baseline = validate_baseline(event_bytes, baseline_bytes)
    snapshot, _ = restore_snapshot(baseline, events, interventions)
    return canonical({"baseline_sha256": digest(baseline_bytes), "frozen": baseline["frozen"],
                      "snapshot": snapshot, "interventions": deepcopy(interventions)})


def visible_input(snapshot, tools):
    """Exact bytes passed to the scripted adapter, with no provenance or ledger."""
    return canonical({"messages": snapshot["messages"], "tools": tools})


def _coverage(event, snapshot):
    intervals = sorted((source["start"], source["end"])
                       for source in snapshot["provenance"]
                       if source["event_id"] == event["event_id"])
    total, right = 0, 0
    for start, end in intervals:
        total += max(0, end - max(start, right))
        right = max(right, end)
    return total


def replay(event_bytes: bytes, baseline_bytes: bytes, proposal_bytes: bytes):
    """Validate the entire comparison first, then invoke the same adapter twice."""
    events, baseline = validate_baseline(event_bytes, baseline_bytes)
    proposal = parse(proposal_bytes)
    keys(proposal, {"baseline_sha256", "frozen", "snapshot", "interventions"}, "proposal")
    require(proposal["baseline_sha256"] == digest(baseline_bytes), "original baseline changed")
    require(canonical(proposal["frozen"]) == canonical(baseline["frozen"]),
            "frozen prefix/environment/tools/adapter/budget changed")
    validate_snapshot(proposal["snapshot"], events, baseline["prefix_length"])
    expected, changes = restore_snapshot(baseline, events, proposal["interventions"])
    require(proposal["snapshot"] == expected, "undeclared context edit")

    # Validation is complete. No adapter call is allowed above this boundary.
    original_input = visible_input(baseline["snapshot"], baseline["frozen"]["tools"])
    replay_input = visible_input(proposal["snapshot"], baseline["frozen"]["tools"])
    original_action = adapter.decide(original_input)
    replay_action = adapter.decide(replay_input)
    frozen = baseline["frozen"]
    evidence = [{"event_id": event["event_id"], "seq": event["seq"],
                 "logged_before_decision": event["seq"] < baseline["prefix_length"],
                 "event_content_bytes": len(event["text"].encode("utf-8")),
                 "visible_original_content_bytes": _coverage(event, baseline["snapshot"]),
                 "visible_replay_content_bytes": _coverage(event, proposal["snapshot"])}
                for event in events]
    def pretty(value):
        return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n").splitlines(True)
    return {
        "report_schema_version": 1,
        "decision_id": baseline["decision_id"],
        "claim_scope": "Scripted adapter sensitivity only; no model or task-quality measurement.",
        "validation": "passed_before_adapter_calls",
        "originals": {"events_file_sha256": digest(event_bytes),
                      "baseline_file_sha256": digest(baseline_bytes)},
        "frozen_hashes": {"prefix_sha256": frozen["prefix_sha256"],
                          "environment_sha256": fingerprint(frozen["environment"]),
                          "tools_sha256": fingerprint(frozen["tools"]),
                          "adapter_sha256": fingerprint(frozen["adapter"])},
        "units": "UTF-8 bytes, not model tokens",
        "original": {"action": original_action, "visible_input_sha256": digest(original_input),
                     "visible_input_bytes": len(original_input),
                     "visible_input_utf8": original_input.decode("utf-8"),
                     "snapshot": deepcopy(baseline["snapshot"])},
        "replay": {"action": replay_action, "visible_input_sha256": digest(replay_input),
                   "visible_input_bytes": len(replay_input),
                   "visible_input_utf8": replay_input.decode("utf-8"),
                   "snapshot": deepcopy(proposal["snapshot"])},
        "action_changed": original_action != replay_action,
        "interventions": changes,
        "evidence_visibility": evidence,
        "snapshot_diff": "".join(unified_diff(pretty(baseline["snapshot"]), pretty(proposal["snapshot"]),
                                              fromfile="original", tofile="replay")),
    }
