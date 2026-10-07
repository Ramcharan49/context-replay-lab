# Agreement is not requirement coverage

**Question:** Can a pool of unanimous answers still fail the task?

Yes. This synthetic task asks for an item's availability **and** its planned
restock date. Three scripted candidates all report the correct availability, but
all omit the date. A comparison restricted to their observed claims sees complete
agreement. An audit that starts from the task's two requirements catches the
omission in every candidate.

This is a second, standalone experiment in the context replay lab. The first
experiment separates *logged evidence* from *visible evidence*. This one separates
*candidate agreement* from *requirement coverage and source support*. Neither
experiment runs a model or measures reasoning quality.

## Run and inspect

From the repository root, using Python 3.10+ and only the standard library:

```sh
python3 -m context_replay verify-demo
python3 -m unittest discover -s tests -v
```

Inspect just the shared-omission case:

```sh
python3 -m context_replay verify \
  --task experiments/shared_omission/task.json \
  --evidence experiments/shared_omission/evidence.json \
  --candidates experiments/shared_omission/shared_omission.candidates.json
```

`verify` exits **0** if at least one candidate has every declared requirement
supported, **1** if none does, and **2** for invalid input or an I/O error. A 0 does
not mean every candidate passed; inspect `supported_candidate_ids`. The omission
command above intentionally exits 1. `verify-demo` exits 0 when all expected
controls behave correctly, including the intentionally failing candidates.

Both commands accept `--out PATH` to create a JSON report and refuse to overwrite
an existing file. [`reports/verification_demo.json`](../../reports/verification_demo.json)
contains the reproducible output for all five controls.

## Controls

All data and candidates are invented, not sampled model outputs. The fixed source
says `availability=unavailable` and `restock_date=2030-01-15`.

| Control | All observed claims unanimous? | Evidence audit |
|---|---|---|
| Three answers omit the date | Yes | Date omitted in all; no candidate supported |
| Three complete, correct answers | Yes | All three fully supported |
| Three answers use the wrong date | Yes | Date contradicted in all |
| Two wrong dates, one correct date | No | Only candidate-3 fully supported |
| Complete answers, date source unavailable | Yes | Date unresolved in all |

The first row is the main counterexample. The second checks that the audit can
accept a valid result. The third shows that coverage alone is insufficient. The
fourth shows that a supported minority can beat an unsupported majority. The
fifth ensures that lack of evidence never silently becomes support.

## Mechanics

[`task.json`](task.json) defines the requirement universe before looking at any
candidate. Each requirement supplies an ID, a description, and a `source_id` plus
`field` locator. It does not embed the expected value.

[`evidence.json`](evidence.json) is a separate, trusted synthetic snapshot with
source IDs and scalar fields. The candidates contain requirement-ID-to-value
claims. This is intentionally structured data; there is no natural-language claim
extraction, semantic equivalence, source discovery, or tool execution.

[`verification.py`](../../context_replay/verification.py) performs these steps:

1. Validate all three inputs before producing verdicts. Reject malformed JSON,
   duplicate keys/IDs, unknown fields or requirement IDs, unsupported versions,
   non-scalar values, empty requirement lists, and empty candidate pools
2. Walk the **task's requirements**, rather than only the union of candidate keys.
   Distinguish a shared omission, a unanimous value, and a disputed value
3. Read the exact declared source field. Mark a missing source or field explicitly;
   do not substitute an unrelated field or guess from candidate frequency
4. Give each candidate/requirement pair one verdict: `omitted`, `supported`,
   `contradicted`, or `unresolved`. An omitted required claim remains a coverage
   failure even if its source is unavailable. A present claim without evidence is
   unresolved, not contradicted
5. List a candidate as fully supported only when **every** requirement is supported.
   Preserve the source locator, source value when available, candidate value,
   verdict, and raw input SHA-256 hashes in the report

Omission means an absent claim key. JSON `null`, `false`, `0`, `0.0`, and `""` are
present values. Equality compares canonical JSON bytes, so `false`, `0`, and `0.0`
are different even though Python can compare them as equal. This conservative
exact-equality rule is part of the experiment, not a universal semantic rule.

Decimal and exponent tokens must preserve their decimal value when decoded to a
float and serialized again. The shared JSON reader rejects underflow such as
`1e-999` (which Python otherwise turns into `0.0`), rounding such as
`9007199254740993.0` (which becomes `9007199254740992.0`), and overflow before any
verdict. Without this gate, distinct source and claim values could falsely appear
supported or unanimous. Ordinary `0.1`, equivalent spellings such as `0.1000`,
and exact integer tokens remain valid; integer and float types remain distinct.
This is a decimal round-trip check, not arbitrary-precision arithmetic or a
requirement that decimals have exact binary representations. It cannot recover
precision already lost by a caller before producing the input JSON.

The numeric [regressions](../../tests/test_json_numbers.py) cover both evidence and
candidate boundaries, CLI rejection without report creation, and replay rejection
before adapter calls. The learning point is that an exact comparison is only as
trustworthy as the decoding step that supplies its values.

The descriptive `observed_claim_agreement` baseline makes no correctness or
completeness claim. It ignores requirements absent from every answer. If no claim
is observed at all, it reports false rather than vacuous unanimity. The full audit
still reports every omitted requirement. No majority-vote winner is substituted
for source evidence, and no automatic revision is applied.

The [tests](../../tests/test_verification.py) include partial omissions, all-empty
answers, false/zero/null distinctions, extra wrong voters, irrelevant source data,
unavailable evidence, strict validation, determinism, input preservation, report
reproducibility, exit codes, and overwrite protection.

## Research connection and limits

Zhang et al., [VeriHarness: Scaling Agentic Verification for Long-Horizon Tasks](https://arxiv.org/abs/2610.00972)
(1 October 2026), motivates this exercise. In
[Section 3.3](https://arxiv.org/html/2610.00972v1#S3.SS3), the consensus challenger
checks for requirements missing from all artifacts. Section 3.2 separately uses
environment evidence to resolve disagreements. The
[official implementation](https://github.com/google-research/veriharness) organizes
model-driven investigations, adjudication, and delivery; the model decides which
checks to perform.

This lab implements only an original deterministic illustration of those ideas.
It supplies explicit machine-readable requirements, evidence locators, and exact
equality checks in advance. It does not reproduce the paper's autonomous checking,
isolated sessions, evidence acquisition, revision, skill evolution, or benchmark
evaluation. No upstream code, model calls, downloaded weights, paid APIs, or
benchmark datasets are used.

Source support is **relative to the supplied task and evidence**. Hashes make the
inputs identifiable; they do not attest truth, freshness, completeness, or
independence. If the task itself omits a requirement, the audit cannot discover it.
If the source snapshot is wrong, an exactly matching answer can be supported but
wrong in the world. Scalar equality also cannot judge prose, units, ranges,
paraphrases, contradictory sources, or application-specific validity. The JSON
reader is not hardened for unbounded hostile input.

**Next question to explore:** How should a verifier behave when two authoritative
source snapshots disagree or one is stale? Answering that needs an explicit source
precedence/freshness policy, not more votes from the same candidate pool.
