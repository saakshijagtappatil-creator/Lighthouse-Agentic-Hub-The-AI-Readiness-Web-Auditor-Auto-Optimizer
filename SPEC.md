# SPEC: Lighthouse Agentic Hub: The AI-Readiness Web Auditor & Auto-Optimizer

**Track:** Agents for Business
**Status:** v2 — Fully implemented and verified sequential ADK workflow. 7 of 7 nodes verified working end-to-end; passing all 11 integration and unit tests.
**Last updated:** 2026-06-23

---

## 1. Purpose & Scope

A CLI-based agentic tool that audits a website or codebase against Google Lighthouse's "Agentic Browsing" category, accessibility, performance, and Generative Engine Optimization (GEO) schema readiness. It autonomously diagnoses failures, applies a bounded set of remediations, and proves the fix worked with a quantified before/after benchmark.

**In Scope:**
- 7-Agent sequential workflow implementing Intake, Pre-remediation Audit, Diagnosis, Remediation Drafting, Remediation Execution, Post-remediation Benchmarking, and Reporting.
- Human-in-the-loop confirmation gate requiring user approval before modifying files.
- Auto-fixing missing/poor `llms.txt`, missing ARIA navigation tree labels, and missing GEO JSON-LD schemas.
- Diagnostic-only auditing of Accessibility (WCAG AA) and Performance (Core Web Vitals), producing developer manual guidelines.
- Production-grade visual HTML report complete with a collapsible "View Failing Code" inspector panel.
- FastMCP Server with path traversal guards, URL validation, and command injection prevention.

**Accuracy constraint:**
This tool never claims to improve search ranking or SEO. All language in diagnosis/report output must use "Agentic Readiness score" or "Lighthouse Agentic Browsing pass/fail," never "ranking" or "SEO."

---

## 2. Global Constraints

| Constraint | Rule |
|---|---|
| Execution model | Sequential chain via `google.adk.agents.sequential_agent.SequentialAgent(sub_agents=[...])`. Deterministic steps are custom `BaseAgent` subclasses; AI steps are `LlmAgent` or subclasses (`SkippableLlmAgent`, `RemediationDraftAgent`). |
| Cost | $0 only. `GEMINI_API_KEY` against AI Studio free tier. GCP project billing must remain disabled for the lifetime of this project. |
| Secrets | No API key, token, or password ever appears in source, logs, or committed files. `.env` is gitignored; `.env.example` documents required vars with placeholder values only. |
| Remediation safety | File-writing remediation runs **only** against the local sandboxed test site or a local repo path the user explicitly provided. Never writes to a remote/public target. |
| Idempotency | Re-running the full graph against an already-remediated target must not duplicate fixes (e.g., must not write a second `llms.txt` block or duplicate ARIA attributes). |
| Observability | Every node logs a structured line prefixed `[INTAKE]` / `[AUDIT]` / `[DIAGNOSIS]` / `[REMEDIATION]` / `[BENCHMARK]` / `[REPORT]` at start and completion. |
| Input source | Intake accepts **either** a local repo path **or** a public URL, mutually exclusive per run. |

---

## 3. Data Contracts (shared types)

These are the Pydantic models passed between agents via shared session state.

```python
class TargetRef(BaseModel):
    source_type: Literal["local_path", "url"]
    value: str  # absolute path or full URL
    resolved_at: str  # ISO 8601 string

class LighthouseFinding(BaseModel):
    check_id: str
    applicable: bool = True
    passed: bool
    raw_score: Optional[float] = None
    details: str
    category: str  # "agentic_browsing", "accessibility", "performance" or "geo_readiness"
    failing_nodes: list[dict] = Field(default_factory=list)

class AuditResult(BaseModel):
    target: TargetRef
    run_at: str
    findings: list[LighthouseFinding]
    raw_json_path: str

class DiagnosisItem(BaseModel):
    check_id: str
    severity: Literal["critical", "moderate", "info"]
    explanation: str
    remediation_type: Literal["llms_txt", "aria_labels", "webmcp_suggestion_only", "geo_schema", "not_auto_fixable"]
    proposed_action: str

class DiagnosisItems(BaseModel):
    items: list[DiagnosisItem]

class DiagnosisResult(BaseModel):
    audit: AuditResult
    items: list[DiagnosisItem]

class AriaLabelSuggestion(BaseModel):
    file_path: str
    selector: str
    element_snippet: str
    aria_label: str

class RemediationDraft(BaseModel):
    llms_txt_content: Optional[str] = None
    aria_suggestions: list[AriaLabelSuggestion] = Field(default_factory=list)
    webmcp_suggestion: Optional[str] = None
    geo_schema_draft: Optional[str] = None

class RemediationAction(BaseModel):
    check_id: str
    file_path: str
    action_taken: Literal["created", "modified", "skipped_already_present", "skipped_unsafe", "skipped_user_rejected"]
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
    summary_line: str
    report_path: str
```

---

## 4. Node Specifications

### 4.1 Intake Agent
- **Type:** Custom `BaseAgent` subclass (`IntakeAgent`), no LLM call.
- **Input:** the user's chat message text.
- **Behavior:**
  - If `waiting_for_confirmation = True` in session state, reads the input as confirmation. If the user sends a new target command (contains `--path` or `--url`), resets all confirmation/audit state keys and initiates a fresh audit. Otherwise, sets `confirmation_response` and resumes.
  - If starting a new run, parses `--path <local repo path>` or `--url <public url>`. Validates target scheme and path traversal boundaries, and populates `target` in state.

### 4.2 Audit Agent
- **Type:** Custom `BaseAgent` subclass (`AuditAgent`), no LLM call.
- **Input:** reads `target` dict from state.
- **Behavior:**
  - Bypassed on Turn 2.
  - Spins up a local server for local directories, runs `lighthouse` CLI against the target with accessibility, performance, and agentic browsing categories.
  - Executes custom HTTP/fs scans for `llms-txt-exists` and `geo-schema-markup`.
  - Parses raw JSON audit findings and failure nodes, saving the resulting `AuditResult` to state under `audit_result`.

### 4.3 Diagnosis Agent
- **Type:** `SkippableLlmAgent` subclass of `LlmAgent`, Gemini 3.1 Flash Lite.
- **Behavior:**
  - Bypassed on Turn 2.
  - Analyzes the findings in `audit_result`. Maps failures to corresponding remediation types. Ensures accessibility and performance findings are set as `not_auto_fixable`. Saves `DiagnosisItems` to state.

### 4.4 Remediation Draft Agent
- **Type:** `RemediationDraftAgent` subclass of `LlmAgent`, Gemini 3.1 Flash Lite.
- **Behavior:**
  - Bypassed on Turn 2.
  - Drafts specific contents for fixes (e.g. `llms.txt` description, CSS selectors/snippets for `aria_suggestions`, JSON-LD script content for `geo_schema_draft`). Uses the `llms-txt-drafting` skill tool.
  - Handles parsing errors by capturing validation tracebacks, updating system instructions, and retrying once. Falls back to empty schemas on repeated errors.

### 4.5 Remediation Execute Agent
- **Type:** Custom `BaseAgent` subclass (`RemediationExecuteAgent`), no LLM call.
- **Behavior:**
  - **Turn 1**: Compiles the list of proposed changes from the draft, formats a monospace Unicode proposal box, yields the box message, sets `waiting_for_confirmation = True`, and exits early.
  - **Turn 2**: If `confirmation_response == "yes"`, applies fixes to local files using BeautifulSoup4. If `confirmation_response != "yes"`, bypasses writes and records all items as `skipped_user_rejected`.

### 4.6 Benchmark Agent
- **Type:** Custom `BaseAgent` subclass (`BenchmarkAgent`), no LLM call.
- **Behavior:**
  - Bypassed on Turn 1.
  - If fixes were applied (`confirmation_response == "yes"`), re-runs Lighthouse CLI against the target, saving results to `after_audit_result`.
  - If fixes were rejected, bypasses execution and copies `audit_result` to `after_audit_result`.

### 4.7 Report Agent
- **Type:** Custom `BaseAgent` subclass (`ReportAgent`), no LLM call.
- **Behavior:**
  - Bypassed on Turn 1.
  - Diffs before/after findings, writes `before.json`, `after.json`, `report.md`, and a highly polished interactive `report.html` complete with collapsible code panels.
  - Saves the HTML report as an ADK artifact, returns the summary block, and clears the confirmation flags from session state.

---

## 5. MCP Server (FastMCP)

Exposes the security-hardened `audit_web_readiness` tool to external IDE clients:
- **Path Traversal Guard:** Restricts local directory target paths strictly to folders residing inside the user's current workspace.
- **Command Injection Prevention:** Uses `shell=False` inside subprocess executions with explicit argument lists, fully neutralizing shell injection vectors.
- **URL Scheme Validation:** Restricts target URLs strictly to `http` or `https` protocols.
- **Read-Only Interface:** Disallows file writes, only exposing read-only audits.
