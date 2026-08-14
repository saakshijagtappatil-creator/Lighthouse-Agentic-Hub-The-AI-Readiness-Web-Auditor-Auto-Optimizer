import os
import json
import pytest

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from workflows_sequential.agent import ReportAgent
from workflows_sequential.models import (
    AuditResult,
    TargetRef,
    RemediationResult,
    RemediationAction,
    DiagnosisResult,
    LighthouseFinding,
)


@pytest.fixture
def temp_sandbox(tmp_path) -> os.PathLike:
    sandbox_dir = tmp_path / "sandbox"
    sandbox_dir.mkdir()
    return sandbox_dir


def test_report_agent_generation(temp_sandbox) -> None:
    target = TargetRef(
        source_type="local_path",
        value=str(temp_sandbox),
        resolved_at="2026-06-23T00:00:00Z"
    )
    
    # Setup findings
    before_findings = [
        LighthouseFinding(
            check_id="llms-txt-exists",
            applicable=True,
            passed=False,
            details="No llms.txt found",
            category="agentic_browsing"
        ),
        LighthouseFinding(
            check_id="geo-schema-markup",
            applicable=True,
            passed=False,
            details="Missing GEO JSON-LD schema",
            category="geo_readiness"
        )
    ]
    
    after_findings = [
        LighthouseFinding(
            check_id="llms-txt-exists",
            applicable=True,
            passed=True,
            details="llms.txt found",
            category="agentic_browsing"
        ),
        LighthouseFinding(
            check_id="geo-schema-markup",
            applicable=True,
            passed=True,
            details="JSON-LD schema markup detected",
            category="geo_readiness"
        )
    ]

    before_audit = AuditResult(
        target=target,
        run_at="2026-06-23T00:00:00Z",
        findings=before_findings,
        raw_json_path="/tmp/before_raw.json"
    )
    
    after_audit = AuditResult(
        target=target,
        run_at="2026-06-23T00:05:00Z",
        findings=after_findings,
        raw_json_path="/tmp/after_raw.json"
    )

    diag_res = DiagnosisResult(
        audit=before_audit,
        items=[]
    )

    remediation = RemediationResult(
        diagnosis=diag_res,
        actions=[
            RemediationAction(
                check_id="llms-txt-exists",
                file_path="llms.txt",
                action_taken="created",
                diff_summary="Created llms.txt"
            ),
            RemediationAction(
                check_id="geo-schema-markup",
                file_path="index.html",
                action_taken="modified",
                diff_summary="Injected JSON-LD"
            )
        ]
    )

    # Initialize agent & runner
    agent = ReportAgent(name="report_agent")
    session_service = InMemorySessionService()
    session = session_service.create_session_sync(
        user_id="test_user",
        app_name="test",
        state={
            "target": target.model_dump(),
            "audit_result": before_audit.model_dump(),
            "after_audit_result": after_audit.model_dump(),
            "remediation_result": remediation.model_dump(),
            "confirmation_response": "yes",
            "waiting_for_confirmation": False,
        }
    )

    runner = Runner(agent=agent, session_service=session_service, app_name="test")
    message = types.Content(role="user", parts=[types.Part.from_text(text="yes")])
    events = list(runner.run(new_message=message, user_id="test_user", session_id=session.id))

    # Retrieve invocation_id from events
    assert len(events) > 0
    invocation_id = events[0].invocation_id

    # Verify report outputs in runs/<invocation_id>
    run_dir = f"runs/{invocation_id}"
    assert os.path.exists(run_dir)

    before_json_path = os.path.join(run_dir, "before.json")
    after_json_path = os.path.join(run_dir, "after.json")
    report_md_path = os.path.join(run_dir, "report.md")
    report_html_path = os.path.join(run_dir, "report.html")

    assert os.path.exists(before_json_path)
    assert os.path.exists(after_json_path)
    assert os.path.exists(report_md_path)
    assert os.path.exists(report_html_path)

    # Verify content formatting
    report_md_content = open(report_md_path).read()
    assert "AGENTIC BROWSING" in report_md_content
    assert "GEO READINESS" in report_md_content
    assert "ACTIONS TAKEN" in report_md_content
    assert "BENCHMARK" in report_md_content

    # Ensure path traversal leaks are prevented (shows folder base name only)
    report_html_content = open(report_html_path).read()
    assert "sandbox" in report_html_content
    # It should NOT print the full path in the title element / text (e.g. /var/folders/...)
    assert str(temp_sandbox) not in report_html_content
