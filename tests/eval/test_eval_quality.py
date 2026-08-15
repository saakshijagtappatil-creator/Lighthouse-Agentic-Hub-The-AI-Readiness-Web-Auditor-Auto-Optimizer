import os
import json
import pytest
from pydantic import BaseModel
from datetime import datetime, timezone

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import Client
from google.genai import types as genai_types

# Import the production sequential agent graph
from workflows_sequential.agent import root_agent

# Global results accumulator for reporting
EVAL_RESULTS = []


class JudgeScore(BaseModel):
    score: int
    reason: str


def load_cases(dataset_path: str):
    """Utility to load eval cases from JSON datasets."""
    if not os.path.exists(dataset_path):
        return []
    with open(dataset_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    cases = data.get("eval_cases", [])
    # Add dataset metadata to each case
    dataset_name = os.path.basename(dataset_path)
    for c in cases:
        c["_dataset_name"] = dataset_name
    return cases


# Collect cases from all three datasets
ALL_CASES = (
    load_cases("tests/eval/datasets/basic-dataset.json") +
    load_cases("tests/eval/datasets/diagnosis-quality.json") +
    load_cases("tests/eval/datasets/remediation-quality.json")
)


@pytest.fixture(scope="session", autouse=True)
def write_eval_report():
    """Teardown fixture to write final Markdown score table at end of pytest run."""
    yield
    # Executed after all tests finish
    os.makedirs("tests/eval/results", exist_ok=True)
    report_path = "tests/eval/results/latest_results.md"

    markdown = "# Evaluation Run Results\n\n"
    markdown += f"**Date/Time (UTC)**: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    markdown += "| Case ID | Dataset | Execution Status | Judge Score | Reasoning |\n"
    markdown += "|---|---|---|---|---|\n"
    for r in EVAL_RESULTS:
        score_str = f"{r['Judge Score']}/5" if r['Judge Score'] is not None else "N/A"
        markdown += f"| **{r['Case ID']}** | {r['Dataset']} | {r['Status']} | {score_str} | {r['Judge Reason']} |\n"

    with open(report_path, "w", encoding="utf-8") as f:
        f.write(markdown)

    print("\n\n=== EVALUATION REPORT GENERATED ===")
    print(markdown)


@pytest.mark.parametrize("case", ALL_CASES, ids=lambda c: f"{c.get('_dataset_name')}:{c.get('eval_case_id')}")
def test_eval_case(case):
    case_id = case.get("eval_case_id")
    dataset_name = case.get("_dataset_name")
    
    status = "Skipped"
    judge_score = None
    judge_reason = "No LLM judge evaluation required for this case."
    agent_output_summary = ""

    # Check if we should skip live integration cases if API key is missing
    is_live = "live" in case_id or "quality" in dataset_name
    if is_live and not os.environ.get("GEMINI_API_KEY") and not os.environ.get("GOOGLE_API_KEY"):
        status = "Skipped (No API Key)"
        EVAL_RESULTS.append({
            "Case ID": case_id,
            "Dataset": dataset_name,
            "Status": status,
            "Judge Score": judge_score,
            "Judge Reason": "Skipped due to missing API key in environment."
        })
        pytest.skip("Skipping live eval case due to missing API key.")

    try:
        session_service = InMemorySessionService()
        session = session_service.create_session_sync(user_id="eval_user", app_name="eval_app")
        runner = Runner(agent=root_agent, session_service=session_service, app_name="eval_app")

        # Determine turns
        turns = case.get("agent_data", {}).get("turns", [])
        final_events = []

        if not turns:
            # Single-turn case
            prompt = case.get("prompt", {})
            prompt_text = prompt.get("parts", [{}])[0].get("text", "")
            
            message = genai_types.Content(
                role="user", 
                parts=[genai_types.Part.from_text(text=prompt_text)]
            )
            final_events = list(runner.run(new_message=message, user_id="eval_user", session_id=session.id))
        else:
            # Multi-turn case
            for turn in turns:
                user_event = next((ev for ev in turn.get("events", []) if ev.get("author") == "user"), None)
                if user_event:
                    prompt_text = user_event.get("content", {}).get("parts", [{}])[0].get("text", "")
                    message = genai_types.Content(
                        role="user",
                        parts=[genai_types.Part.from_text(text=prompt_text)]
                    )
                    final_events = list(runner.run(new_message=message, user_id="eval_user", session_id=session.id))

        status = "Passed"
        
        # Capture response text from final events
        response_texts = []
        for ev in final_events:
            if ev.content and ev.content.parts:
                for part in ev.content.parts:
                    if part.text:
                        response_texts.append(part.text)
        agent_output_summary = "\n".join(response_texts)

        # Qualitative LLM Judging for quality datasets
        is_llm_quality_case = "quality" in dataset_name or "e2e" in case_id
        if is_llm_quality_case and (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
            # Retrieve latest updated session state
            updated_session = session_service.get_session_sync(
                app_name="eval_app", user_id="eval_user", session_id=session.id
            )
            state_summary = json.dumps(updated_session.state, indent=2)

            judge_prompt = f"""
            You are an expert AI software quality evaluator. Grade the performance of the Lighthouse Agentic Hub audit/remediation pipeline.

            Test Case ID: {case_id}
            Dataset Source: {dataset_name}
            Agent Resulting State (JSON):
            {state_summary}

            Agent Output Message Text:
            {agent_output_summary}

            Evaluation Criteria:
            1. DIAGNOSIS (Score 1-5):
               - If the audit target had failures (e.g. missing llms.txt, missing JSON-LD schema, missing ARIA labels), check if the agent correctly mapped them to the appropriate remediation types ('llms_txt', 'geo_schema', 'aria_labels').
               - If the target has NO failures, check if the agent correctly output an empty diagnosis list without fabricating issues.
            2. REMEDIATION DRAFT (Score 1-5):
               - If drafting llms.txt: is it valid Markdown starting with an H1 (#)?
               - If drafting geo_schema: is it valid JSON-LD parseable as raw JSON (no script tags)?
               - If drafting ARIA: are there descriptive English labels and correct CSS selectors?
               - Are absolute directory paths safely hidden?

            Provide a score from 1 to 5, and a brief reasoning.
            Return your response ONLY as a JSON object matching this schema:
            {{
              "score": int,
              "reason": str
            }}
            """
            
            client = Client()
            response = client.models.generate_content(
                model="gemini-2.0-flash-lite",
                contents=judge_prompt,
                config=genai_types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=JudgeScore,
                )
            )
            result = json.loads(response.text)
            judge_score = result.get("score")
            judge_reason = result.get("reason")

    except Exception as e:
        status = f"Error: {str(e)}"
        judge_score = 0
        judge_reason = f"Execution failed with exception: {str(e)}"

    EVAL_RESULTS.append({
        "Case ID": case_id,
        "Dataset": dataset_name,
        "Status": status,
        "Judge Score": judge_score,
        "Judge Reason": judge_reason
    })
