# Context replay lab

**Can we change the context at one decision without quietly changing the experiment?**

A small, original Python-standard-library lab for that question. It captures a
synthetic event history, records the exact input seen at a decision, and permits
only declared restoration of earlier evidence. A validation gate rejects changed
history, frozen-state confounds, future evidence, and unaccounted-for edits before
either replay arm reaches the adapter.

The adapter is an explicitly scripted inventory rule. There is **no LLM, model
download, inference, paid API, benchmark score, or self-evolution loop** here. All
data is invented. No implementation from a paper or other repository is copied.

## Run it

Python 3.10+; tested on Python 3.12.14. Run from the repository root. No install or
third-party dependencies are needed.

Git attributes preserve LF line endings on every platform, including Windows
with `core.autocrlf=true`, because source and capture hashes depend on exact
file bytes. JSON reports use UTF-8 and LF on stdout as well as with `--out`.
The checkout regression additionally runs when Git is available.

```sh
python3 -m unittest discover -s tests -v
python3 -m context_replay demo
```

The full deterministic output is checked in at [`reports/demo.json`](reports/demo.json).
To create another report without overwriting anything:

```sh
python3 -m context_replay demo --out /tmp/context-replay-demo.json
```

An existing output file is refused. To inspect a single replay:

```sh
python3 -m context_replay replay \
  --events examples/events.jsonl \
  --baseline examples/baseline.json \
  --proposal examples/positive_restore.proposal.json
```

## Companion experiment: agreement can hide a shared omission

What if three candidate answers agree because all three forgot the same requested
detail? The [shared-omission experiment](experiments/shared_omission/README.md)
compares observed-claim agreement with a requirement-first, evidence-linked audit:

```sh
python3 -m context_replay verify-demo
```

It adds five deterministic controls: shared omission, complete answers, a shared
wrong value, a correct minority, and missing evidence. The verifier checks every
declared requirement, including ones absent from every candidate. Missing evidence
stays unresolved. Reports show each claim, source field, and verdict.

This is an original, model-free teaching exercise inspired by VeriHarness, not a
reproduction or a model-quality evaluation. The [walkthrough](experiments/shared_omission/README.md)
explains the distinction and the connection to the earlier context-replay lab.
The checked output is [`reports/verification_demo.json`](reports/verification_demo.json).

## The experiment

The ledger has five events. The decision occurs after the first four:

1. `e0000`: a request to check synthetic item DEMO-7
2. `e0001`: a tool observation containing `inventory_status=unavailable`
3. `e0002`: an irrelevant catalog note
4. `e0003`: a summary that omits the inventory status
5. `e0004`: a later restock observation, **after the decision boundary**

The original visible snapshot contains only events 0 and 3. The ledger knows
that the item was unavailable, but the adapter's input does not contain that
observation. This is the distinction the lab makes inspectable: **logged evidence
is not automatically visible evidence**.

The fixed rule requests an inventory check when no unambiguous status is visible.
Restoring event 1 lets the same rule report the unavailable status. The tool is
not actually executed; these strings describe the next action only.

| Case | Exact visible-input bytes | Original action → replay action |
|---|---:|---|
| Restore omitted inventory evidence | 369 → 442 | request_inventory_check → report_unavailable |
| Restore irrelevant catalog note | 369 → 455 | request_inventory_check → request_inventory_check |
| Restore an already-visible request | 369 → 369 | request_inventory_check → request_inventory_check |
| Leave everything unchanged | 369 → 369 | request_inventory_check → request_inventory_check |

These counts include the canonical JSON request envelope and frozen tool schema.
The positive restoration adds **43 content bytes**, or **73 serialized input
bytes** including its message structure. Neither quantity is a token count.

The positive control shows that the intervention reaches the intended interface.
The irrelevant control shows that adding any message does not necessarily change
the scripted action. The two exact-input controls check no-op handling and repeat
behavior. This is deliberately constructed adapter sensitivity, not evidence of
better reasoning, reduced hallucination, or improved task completion.

## Walk through the implementation

### 1. Capture first; analyze later

[`examples/events.jsonl`](examples/events.jsonl) is an append-only event ledger.
Every row has a sequence number, stable ID, role, text, previous hash, and content
hash. `append_event` only appends after checking the current chain and new event.
Replay reads immutable bytes and has no write path into the original capture.
Ledger records are separated by LF (CRLF is accepted); bare CR cannot separate
records. CR whitespace within a JSON row is preserved. Blank records are rejected,
and one final newline is optional when reading. The append helper requires it.

[`examples/baseline.json`](examples/baseline.json) freezes the decision boundary,
canonical execution-prefix hash, declared environment, tool definitions, adapter
identity, and restoration budget. Its snapshot records both the actual message
content and a source event plus half-open UTF-8 byte interval for every message.
The message text and role must exactly match that source. Missing provenance is
an error, not permission to reconstruct a plausible substitute.

### 2. Declare the smallest intervention

The positive proposal declares just one operation:

```json
{"op":"restore","source":{"event_id":"e0001","start":0,"end":43}}
```

The permitted transformation appends that exact historical span with its original
role. Existing messages cannot be removed, rewritten, or reordered. A span already
contained in one visible message is a no-op. Partial spans are allowed, and the
report counts only the source bytes actually visible. Duplicate declarations and
restoration above the frozen content-byte budget are rejected.

`make_proposal` is a convenience builder, not a bypass: `replay` independently
derives the permitted result and compares it with the submitted snapshot.

### 3. Validate before either decision

[`context_replay/core.py`](context_replay/core.py) checks:

- Strict JSON and schemas, event order, uniqueness, and hash-chain integrity
- The captured prefix against the current ledger
- The proposal's exact baseline-file hash and frozen-state consistency
- The adapter's source-file hash and declared settings
- Every snapshot message against its event/span provenance
- Every restored source against the decision boundary, excluding later events
- The entire candidate snapshot against only the declared operations

Only after all checks pass are the two exact serialized inputs passed to the
same adapter. Its interface receives messages and frozen tool definitions, with
no ledger, future events, provenance, or environment snapshot. The report saves
those input strings, their hashes, byte counts, both decisions, source visibility,
and a unified snapshot diff.

### 4. Try falsifying the explanation

Read [`tests/test_replay.py`](tests/test_replay.py). Rejection tests spy on the
adapter and assert **zero calls**, so invalid experiments cannot produce an
apparently meaningful pair of decisions. Cases cover changed environment/tools/
adapter/prefix, a recomputed event chain, future-source injection, fabricated
content, undeclared edits, missing provenance, invalid byte spans, malformed JSON,
and budgets. Other tests check repeat determinism, original-file preservation,
append-only extension, no-op handling, CLI refusal to overwrite, and exact adapter
inputs. A regression test catches Python's `False == 0 == 0.0` pitfall by requiring
canonical JSON equality for frozen state.

For a fresh capture using the current scripted adapter:

```sh
python3 -m context_replay init-fixture /tmp/new-context-capture
python3 -m context_replay demo --fixtures /tmp/new-context-capture
```

The directory must not already exist. If the adapter implementation is edited,
old captures intentionally fail its identity check. Create a new capture rather
than silently refreshing old fingerprints.

## What this does and does not establish

- **Consistency, not world attestation.** Hashes detect differences relative to a
  trusted capture. They do not prove that the recorded environment matched a real
  external system. Someone who fabricates both capture and proposal can fabricate
  agreement. No signatures or trusted hardware are provided.
- **One decision, not a task counterfactual.** Nothing is resumed afterward. A
  changed next action does not establish a different final outcome.
- **A provider-independent teaching interface.** The exact recorded bytes are
  those received by this adapter; they are not a captured production model API
  request, tokenizer stream, hidden prompt, or attention trace.
- **No inference about model quality.** The rule was written to respond to the
  restored status. There is no sampled model variation, confidence interval,
  automatic failure attribution, policy search, or held-out evaluation.
- **No live tools or environment rollback.** Frozen tool/environment objects are
  records. This code does not restore a container, filesystem, remote service,
  wall clock, or tool implementation. Tool versions and schemas alone cannot
  attest real tool behavior.
- **Source fidelity is narrower than semantic fidelity.** Exact span matching
  prevents fabricated bytes; it does not detect misleading excerpts, stale or
  contradictory evidence, prompt injection, or selection based on hindsight.
  Inspecting a full ledger that includes future events can still bias a human
  analyst's intervention choice; the gate blocks future content in the input,
  not that analyst-level selection effect.
- **Source-file identity is not runtime attestation.** It does not detect an
  in-memory monkey patch, altered interpreter, compromised imports, or side
  channels. Run only in a trusted process. The JSON reader is not hardened for
  unbounded hostile input.
- **Small, single-writer storage.** The append helper is neither concurrent nor
  crash-atomic. Replay permits valid later appends without altering its prefix;
  the report's whole-ledger hash still changes so the audit records that fact.
- **Bounded numeric representation.** Decimal/exponent JSON numbers must retain
  their decimal value through float serialization. The reader rejects underflow,
  overflow, and rounding that would silently merge distinct values. Integers stay
  exact within Python's input limits. This is not arbitrary-precision decimal
  support; precision lost before JSON capture cannot be recovered. See the
  [numeric verification walkthrough](experiments/shared_omission/README.md#mechanics).

## Research connection and primary sources

[ContextEvo: Beyond Skill Evolution: Self-Evolving Context Management Policies for
Long-Horizon Agent Harnesses](https://arxiv.org/html/2609.34649v1), Weiyuan Li et al.,
28 September 2026, motivates the question. Section 4.2 holds the execution prefix
fixed while editing the context at one decision. Appendix B.2 distinguishes local
decision sensitivity from task-level outcomes. The paper's broader pipeline also
reconstructs evidence, diagnoses failures, and updates context policies. This lab
implements only an original, much narrower consistency-checking exercise; it is
not a reproduction of that pipeline or its reported results. No official
implementation link was verified for this session.

The library behavior used here is documented by Python's
[JSON](https://docs.python.org/3/library/json.html),
[hashlib](https://docs.python.org/3/library/hashlib.html), and
[unittest](https://docs.python.org/3/library/unittest.html) documentation.

## Next useful experiment

Add several independently designed synthetic decision tasks and ask whether the
same restoration rule helps all of them, including cases with stale or conflicting
past observations. Predeclare the intervention before looking at later outcomes.
Keep that separate from any future real-model experiment, which would additionally
need fixed model/request settings, paired repeats, a sampling/noise baseline, a
held-out evaluation, a cost budget, and a trusted replayable environment.
