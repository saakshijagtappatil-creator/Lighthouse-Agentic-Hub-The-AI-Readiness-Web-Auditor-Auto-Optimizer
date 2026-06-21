# SPEC: AI Agent Readiness Auditor & Optimizer

**Track:** Agents for Business
**Status:** v2 — revised against the REAL installed ADK API after initial
template mismatch discovered during implementation. 3 of 5 nodes confirmed
working end-to-end (Intake, Audit-stub, Diagnosis); Remediation, Benchmark,
Report still pending real logic.
**Last updated:** 2026-06-20

**Revision note:** v1 of this spec assumed `WorkflowAgent` with tuple-based
`edges` (an API pattern from a newer ADK version than what's actually
installed). That template doesn't exist in `google-adk==1.35.2`, the
confirmed installed version. This spec is now corrected to match the real,
tested API: `SequentialAgent` + custom `BaseAgent` subclasses. See §2 and
§3 for what changed and why — left visible rather than silently editing
history, since "we corrected course honestly when reality didn't match the
plan" is itself evidence of real spec-driven development for the writeup.

---

## 1. Purpose & Scope

A CLI-based agentic tool that audits a website or codebase against Google
Lighthouse's "Agentic Browsing" category (Lighthouse 13.3+), autonomously
diagnoses failures, applies a bounded set of remediations, and proves the
fix worked with a quantified before/after benchmark.

**Explicitly NOT in scope for v1:**
- Live deployment to a public endpoint (not required by rubric; deployability
  is demonstrated via video only)
- WebMCP *implementation* (only diagnosis + textual suggestion — see §4.4)
- CLS / layout-stability remediation (diagnosed, not auto-fixed — too risky
  to safely auto-edit CSS/layout without visual regression testing)
- Any write operation against a real public URL (remediation is local-only,
  always)

**Accuracy constraint (binding on all agent prompts and all written output):**
This tool never claims to improve search ranking or SEO. All language in
diagnosis/report output must use "Agentic Readiness score" or "Lighthouse
Agentic Browsing pass/fail," never "ranking" or "SEO."

---

## 2. Global Constraints

| Constraint | Rule |
|---|---|
| Execution model | Sequential chain via `google.adk.agents.sequential_agent.SequentialAgent(sub_agents=[...])`, confirmed against `google-adk==1.35.2`. Deterministic steps are custom `BaseAgent` subclasses (NOT plain functions — `SequentialAgent` only accepts real Agent objects); AI steps are `LlmAgent`. No parallel steps — respects free-tier rate limits (~15 RPM / 1,500 RPD on Gemini Flash). |
| Cost | $0 only. `GEMINI_API_KEY` against AI Studio free tier. GCP project billing must remain disabled for the lifetime of this project. |
| Secrets | No API key, token, or password ever appears in source, logs, or committed files. `.env` is gitignored; `.env.example` documents required vars with placeholder values only. |
| Remediation safety | File-writing remediation runs **only** against the local sandboxed test site or a local repo path the user explicitly provided. Never writes to a remote/public target. |
| Idempotency | Re-running the full graph against an already-remediated target must not duplicate fixes (e.g., must not write a second `llms.txt` block or duplicate ARIA attributes). |
| Observability | Every node logs a structured line prefixed `[INTAKE]` / `[AUDIT]` / `[DIAGNOSIS]` / `[REMEDIATION]` / `[BENCHMARK]` / `[REPORT]` at start and completion, with elapsed time and pass/fail status. |
| Input source | Intake accepts **either** a local repo path **or** a public URL, mutually exclusive per run (see §4.1). |

---

## 3. Data Contracts (shared types)

These are the Pydantic models passed between agents via shared session
state. All inter-agent payloads are one of these — no untyped dicts.

**Confirmed state-passing mechanism (corrected from v1):** state is NOT
passed via a `node_input` parameter (that was a v1 assumption that turned
out to be wrong for this installed version). The real, tested mechanism:
each custom agent yields
`Event(author=self.name, invocation_id=ctx.invocation_id, actions=EventActions(state_delta={key: value}))`
to write, and reads via `ctx.session.state.get(key)`. Session state is
persisted to SQLite, so **only JSON-serializable values go in
`state_delta`** — plain dicts via `.model_dump()`, never raw Pydantic
objects directly.

**Timestamp fix:** all `datetime` fields below were changed to plain ISO
8601 strings (`str`). The original `datetime` typing caused a real,
confirmed failure: `Object of type datetime is not JSON serializable`,
hit when an `LlmAgent`'s structured output (auto-saved to state) contained
a nested datetime value. Strings avoid this category of bug everywhere.

```python
class TargetRef(BaseModel):
    """What we're auditing."""
    source_type: Literal["local_path", "url"]
    value: str  # absolute path or full URL
    resolved_at: str  # ISO 8601 string, e.g. datetime.now(timezone.utc).isoformat()

class LighthouseFinding(BaseModel):
    check_id: str  # e.g. "llms-txt-present", "webmcp-detected", "a11y-tree-integrity", "cls-score"
    passed: bool
    raw_score: float | None  # 0.0-1.0 where applicable, None for boolean checks
    details: str  # raw Lighthouse explanation, unmodified

class AuditResult(BaseModel):
    target: TargetRef
    run_at: str  # ISO 8601 string
    findings: list[LighthouseFinding]
    raw_json_path: str  # where the full Lighthouse JSON was saved

class DiagnosisItem(BaseModel):
    check_id: str
    severity: Literal["critical", "moderate", "info"]
    explanation: str  # why this matters for agentic browsing specifically
    remediation_type: Literal["llms_txt", "aria_labels", "webmcp_suggestion_only", "not_auto_fixable"]
    proposed_action: str  # human-readable description of the planned fix

class DiagnosisItems(BaseModel):
    """What the LlmAgent actually produces — NOT a full DiagnosisResult.

    Added in v2: the LLM must never be asked to fabricate AuditResult data
    it wasn't given. The real DiagnosisResult is assembled in Python by
    combining the real AuditResult with these items, once Audit produces
    real data.
    """
    items: list[DiagnosisItem]

class DiagnosisResult(BaseModel):
    audit: AuditResult
    items: list[DiagnosisItem]

class RemediationAction(BaseModel):
    check_id: str
    file_path: str
    action_taken: Literal["created", "modified", "skipped_already_present", "skipped_unsafe"]
    diff_summary: str

class RemediationResult(BaseModel):
    diagnosis: DiagnosisResult
    actions: list[RemediationAction]

class BenchmarkComparison(BaseModel):
    check_id: str
    before_passed: bool
    after_passed: bool
    delta: Literal["fixed", "unchanged_pass", "unchanged_fail", "regressed"]

class FinalReport(BaseModel):
    target: TargetRef
    before: AuditResult
    after: AuditResult
    comparisons: list[BenchmarkComparison]
    summary_line: str  # e.g. "3 of 4 Agentic Readiness checks fixed"
    report_path: str  # where before.json/after.json/report.md were written
```

---

## 4. Node Specifications

### 4.1 Intake Agent — ✅ CONFIRMED WORKING

- **Type:** Custom `BaseAgent` subclass (`IntakeAgent`), no LLM call
- **Input:** the user's chat message text, either `--path <local repo path>` or `--url <public url>` (mutually exclusive; error if both or neither given)
- **Output:** writes `{"target": TargetRef.model_dump()}` to state via `EventActions(state_delta=...)`
- **Behavior:**
  - If `local_path`: verify path exists and is a directory; resolve to absolute path.
  - If `url`: verify it's a well-formed `http(s)://` URL; no network call yet (that happens in Audit).
- **Failure mode:** sends a clear chat message and returns early — never silently defaults.
- **Success criteria:** ✅ tested live via `agents-cli playground`, `--path /tmp` correctly resolved and written to state.

### 4.2 Audit Agent — ⏳ SHAPE CONFIRMED, REAL LOGIC PENDING

- **Type:** Custom `BaseAgent` subclass (`AuditAgent`), no LLM call
- **Input:** reads `target` dict from state, reconstructs `TargetRef`
- **Output:** (pending) `AuditResult`, written to state
- **Behavior (still to build):**
  - For `local_path`: serve the directory via the Docker sandbox web server (nginx), then run `lighthouse <local-served-url> --only-categories=agentic-browsing --output=json`.
  - For `url`: run Lighthouse directly against the given URL, **read-only**, no write side effects regardless of target.
  - Save raw Lighthouse JSON to `runs/<timestamp>/audit_raw.json`; populate `raw_json_path`.
  - Parse the four known checks (`llms-txt-present`, `webmcp-detected`, `a11y-tree-integrity`, `cls-score`) into `LighthouseFinding` objects.
- **Failure mode:** If `lighthouse` binary is missing or the audited target is unreachable, raise a clear error naming the missing dependency — do not fabricate findings.
- **Success criteria:** Produces an `AuditResult` with exactly 4 findings (one per known check) on any reachable target, local or remote. Currently only a stub confirming it can read `target` from state correctly (✅ confirmed live).

### 4.3 Diagnosis Agent — ✅ SHAPE CONFIRMED, AWAITING REAL AUDIT DATA

- **Type:** `LlmAgent` with `output_schema=DiagnosisItems` (not the full `DiagnosisResult` — see §3 for why), Gemini 2.5 Flash
- **Input:** reads `audit_result` from state (once Audit produces real data)
- **Output:** `DiagnosisItems`, written to state under `output_key="diagnosis_items"`. The full `DiagnosisResult` (with the real `AuditResult` attached) is assembled in Python once Audit is real — not requested from the LLM.
- **Behavior:**
  - For each failing finding, the LLM explains *why it matters for agentic browsing* in plain language (not generic SEO language — see §1 accuracy constraint), assigns severity, and proposes a `remediation_type`.
  - `cls-score` always maps to `remediation_type="not_auto_fixable"` (out of scope per §1).
  - `webmcp-detected` failures always map to `remediation_type="webmcp_suggestion_only"`.
  - Passing findings still appear in `items` with `severity="info"` and a short confirmation, for report completeness.
  - **Confirmed via live test:** with no real audit data available, correctly returns a single honest placeholder item rather than fabricating findings — validates the "don't ask the LLM to invent data" design choice.
- **Failure mode:** If the LLM output fails Pydantic validation, retry once with the validation error appended to the prompt; if it fails twice, mark that check as `remediation_type="not_auto_fixable"` and continue rather than crashing the run.
- **Success criteria:** Every finding from input `AuditResult` has exactly one corresponding `DiagnosisItem`; no finding is dropped. ✅ Schema validation and state read/write confirmed live; full behavior pending real audit data.

### 4.4 Remediation Agent — ⏳ NOT YET BUILT

- **Type:** `LlmAgent` (drafts content) + custom `BaseAgent` subclass (performs the actual file write) — same hybrid split as before, now using the confirmed real API instead of the assumed one.
- **Input:** `DiagnosisResult`
- **Output:** `RemediationResult`
- **Behavior (v1 scope, per your decision — llms_txt and aria_labels only):**
  - `remediation_type="llms_txt"`: LLM drafts `llms.txt` content describing the site/repo purpose and key routes; Python tool writes it to the target root **only if no `llms.txt` already exists** (else `action_taken="skipped_already_present"`).
  - `remediation_type="aria_labels"`: LLM identifies missing/weak ARIA attributes from the accessibility tree dump; Python tool (BeautifulSoup) injects `aria-label`/`role` attributes into matched elements, writing only well-formed, scoped attribute additions — never restructures the DOM.
  - `remediation_type="webmcp_suggestion_only"`: no file write. The proposed WebMCP integration snippet is captured as text in `diff_summary` only, surfaced in the final report.
  - `remediation_type="not_auto_fixable"`: no file write; `action_taken="skipped_unsafe"`.
  - All file writes happen only against the local sandbox or user-provided local path — **never** against a `source_type="url"` target. If the original `TargetRef.source_type == "url"`, every action is forced to one of the `skipped_*` outcomes regardless of `remediation_type`, and this is logged explicitly.
- **Failure mode:** Any file write that would overwrite existing non-empty content (other than the documented "append if absent" llms.txt case) is refused and logged as `skipped_unsafe`.
- **Success criteria:** For a local target, at least the `llms_txt` and `aria_labels` checks that were diagnosable produce a real, valid file change; for a URL target, zero files are written and this is verifiable in logs.

### 4.5 Benchmark Agent — ⏳ NOT YET BUILT

- **Type:** Custom `BaseAgent` subclass, re-invokes the Audit Agent's underlying logic (same code as §4.2, not a new node type)
- **Input:** `RemediationResult` (specifically `RemediationResult.diagnosis.audit.target`)
- **Output:** `AuditResult` (the "after" result)
- **Behavior:** Re-runs Lighthouse against the same target (re-serving the local sandbox if applicable) to get a fresh, independent reading.
- **Success criteria:** Produces a structurally identical `AuditResult` to the "before" audit, enabling direct comparison.

### 4.6 Report Agent — ⏳ NOT YET BUILT

- **Type:** Custom `BaseAgent` subclass, no LLM call
- **Input:** before `AuditResult` + after `AuditResult` + `RemediationResult`
- **Output:** `FinalReport`
- **Behavior:**
  - Diff before/after per check_id into `BenchmarkComparison` entries.
  - Write `runs/<timestamp>/before.json`, `after.json`, and a human-readable `report.md` summarizing fixes, skips, and remaining manual-fix items (CLS, WebMCP suggestion text).
  - `summary_line` is a single quantified sentence, e.g. "2 of 4 Agentic Readiness checks fixed; 1 unchanged (manual fix required); 1 suggestion provided (WebMCP)."
- **Success criteria:** `report.md` is fully self-contained and readable without needing to inspect raw JSON — this is what gets screen-recorded for the capstone video.

---

## 5. End-to-End Success Criteria (project-level)

The project is considered "working" when, in a single command run against
the Docker sandbox "Broken Website":

1. All 4 checks are correctly audited before any remediation.
2. At least the `llms.txt`-missing and ARIA-related failures are
   autonomously fixed without human intervention.
3. A second audit run shows those specific checks flipping from fail → pass.
4. `report.md` clearly states the before/after delta in plain language with
   no SEO/ranking language anywhere in the output.
5. The same run, pointed at a real public URL via `--url`, completes
   audit + diagnosis successfully and writes **zero** files anywhere.
6. No `GEMINI_API_KEY` or any secret appears in `report.md`, logs, or any
   committed file.

---

## 6. Open Decision (deferred, not blocking)

**MCP Server vs. plain Python tool for the Lighthouse wrapper:** deferred
until after this spec is implemented and the graph runs end-to-end with the
plain-function version. Since the project already covers 5/6 rubric
concepts, this is a stretch goal, not a blocker — revisit once §4.2–4.6 are
working and there's time budget left before July 6.

---

## 7. File Layout (actual, confirmed)

Corrected from v1's assumed `src/` layout — the real `agents-cli create`
scaffold (`adk-samples` template `workflow-sequential`) puts everything
under a package directory matching the project's app name, which the ADK
web server requires for routing. Renaming this directory later is possible
but requires updating `App(name=...)` to match — confirmed the hard way
(see incident log, two debugging sessions lost to an app-name mismatch).

```
ai-readiness-v2/
├── SPEC.md
├── README.md                    # TODO — write once graph is fully real
├── .gitignore
├── pyproject.toml                # has [tool.hatch.build.targets.wheel]
│                                  # packages=["workflows_sequential"] —
│                                  # required fix, hatchling can't infer
│                                  # the package name from "workflow-sequential"
├── uv.lock
├── tests/
└── workflows_sequential/         # the actual package — name MUST match
    ├── __init__.py                # App(name=...) or ADK web server 500s
    ├── agent.py                   # root_agent + all node definitions
    ├── models.py                  # the Pydantic contracts from §3
    └── .env                       # GOOGLE_API_KEY, GOOGLE_GENAI_USE_VERTEXAI=FALSE
                                    # (gitignored — never commit)
```

**Still pending, not yet added:**
- `sandbox/` — Docker container serving the "Broken Website" test site
- `runs/` — gitignored, timestamped output per run
- A real `.env.example` documenting required vars with placeholder values
