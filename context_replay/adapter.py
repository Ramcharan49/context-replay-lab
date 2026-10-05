"""A deliberately scripted decision rule, NOT a model or quality benchmark.

Only the serialized visible input crosses the adapter boundary. The rule cannot
read the event ledger, reference metadata, frozen environment, or later events.
"""

import hashlib
import json
from pathlib import Path


def identity():
    """Consistency fingerprint of this file and the declared adapter settings."""
    return {
        "name": "scripted-inventory-rule",
        "version": "1",
        "implementation_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "settings": {"sampling": "none", "network": "none", "model": "none"},
    }


def decide(visible_input: bytes) -> str:
    """Return a fixed next action from exact inventory lines in tool messages.

    A conflicting status is deliberately handled conservatively.
    Tool schemas are part of the input but this toy rule does not execute tools.
    """
    request = json.loads(visible_input)
    statuses = {
        line
        for message in request["messages"]
        if message["role"] == "tool"
        for line in message["content"].splitlines()
        if line in {"inventory_status=unavailable", "inventory_status=available"}
    }
    if statuses == {"inventory_status=unavailable"}:
        return "report_unavailable"
    if statuses == {"inventory_status=available"}:
        return "report_available"
    return "request_inventory_check"
