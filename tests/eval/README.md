# Lighthouse Agentic Hub: Evaluation Suite

This directory contains the automated evaluation framework used to measure the AI quality, reasoning accuracy, and code-patching reliability of the **Lighthouse Agentic Hub** (`ai-readiness-v2`).

---

## ⚠️ Architectural Note: Pytest vs. `agents-cli`

In the standard Agent Development Kit (ADK) runtime, agents can accept a callable function for their instruction prompt (e.g. `instruction=_diagnosis_instruction`), which allows them to read live, dynamic session state (like Lighthouse audit findings) at execution time.

However, the `agents-cli eval` validation schema expects a static **string** for the `instruction` property:
```text
ValidationError: 1 validation error for AgentConfig
instruction
  Input should be a valid string
  input_value=<function _diagnosis_instruction at 0x...>
```

Because of this parser limitation in the command-line evaluation utility, running `agents-cli eval generate` fails on dynamic prompt architectures. 

To solve this, we use a **custom pytest-based evaluation suite** (`test_eval_quality.py`). This allows us to:
1.  Verify the actual production agents (under `workflows_sequential/agent.py`) without modifying their code or turning dynamic instructions into static prompts.
2.  Run evaluations locally using standard environment variables (`GEMINI_API_KEY`) without requiring complex GCP Application Default Credentials (ADC) or Cloud Storage (GCS) setups.

---

## What Each Dataset Measures

We have defined three separate datasets to evaluate the agents across different granularities:

### 1. Basic Dataset (`datasets/basic-dataset.json`)
*   **Target**: The entire sequential workflow (Intake, Audit, Execution, and Reporting).
*   **What it measures**:
    *   **Intake boundaries**: Valid paths/URLs are accepted, and invalid paths or path traversal attacks are rejected.
    *   **Execution outcomes**: Changes are written on user consent (`yes`), skipped on user rejection (`no`), and marked read-only/unsafe on URL scans.
    *   **Report formatting**: Ensures the interactive report handles comparison diffs correctly and filters out absolute path leaks.
    *   **End-to-End sanity**: Audits the Luminary sandbox site successfully.

### 2. Diagnosis Quality Dataset (`datasets/diagnosis-quality.json`)
*   **Target**: The `DiagnosisAgent` (an LLM node mapping Lighthouse checks to remediation paths).
*   **What it measures**:
    *   **Mapping correctness**: Ensures specific check failures map to the correct remediation types (e.g., `llms-txt-exists` maps to `llms_txt`, `geo-schema-markup` maps to `geo_schema`, and `agent-accessibility-tree` maps to `aria_labels`).
    *   **False positive guard**: Checks that error-free or fully-remediated audit results yield an empty diagnosis array, preventing the agent from hallucinating arbitrary issues.
    *   **Mixed targets**: Verifies mapping handling when presented with mixed fixable and manual findings.

### 3. Remediation Quality Dataset (`datasets/remediation-quality.json`)
*   **Target**: The `RemediationDraftAgent` (an LLM node drafting code modifications).
*   **What it measures**:
    *   **llms.txt formatting**: Drafted content is valid markdown starting with an `# H1` header.
    *   **JSON-LD validation**: Structured schemas are returned as valid, parseable JSON blocks.
    *   **ARIA attributes**: Suggested ARIA label values are written in descriptive English and reference correct CSS selectors.
    *   **Path safety**: Confirms suggestions do not generate hallucinated file paths outside the target scope.

---

## How to Run Evals

To execute the evaluations locally, run the automated execution script:

```bash
./tests/eval/run_evals.sh
```

This runs the custom pytest suite. Individual cases are parameterized using the JSON dataset definitions. If a case times out or encounters an error, the suite skips the failure gracefully so the rest of the cases can complete.

---

## Evaluation Scoring and LLM Judge

Qualitative cases (in the `diagnosis-quality` and `remediation-quality` datasets) are graded automatically on a **1 to 5 scale** using `gemini-2.0-flash-lite` as an LLM judge:

*   **Score 5**: Output matches the criteria perfectly (e.g. valid markdown with headers, valid parseable JSON-LD, correct remediation mapping, zero path leaks).
*   **Score 4**: Output is functional and correct but contains minor stylistic or verbose issues.
*   **Score 1–3**: Output is incorrect, malformed, maps to the wrong remediation category, or hallucinated details/paths.

### Automated Score Reporting
After running the tests, a markdown report table is automatically created under `tests/eval/results/latest_results.md` listing the results, scores, and judge reasoning for every test case.
