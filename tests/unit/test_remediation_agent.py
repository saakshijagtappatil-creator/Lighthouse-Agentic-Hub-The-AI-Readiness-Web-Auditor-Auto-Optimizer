import os
import pytest
from bs4 import BeautifulSoup

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from workflows_sequential.agent import RemediationExecuteAgent
from workflows_sequential.models import (
    AuditResult,
    TargetRef,
    DiagnosisItems,
    DiagnosisItem,
    RemediationDraft,
    AriaLabelSuggestion,
)


@pytest.fixture
def temp_sandbox(tmp_path) -> os.PathLike:
    sandbox_dir = tmp_path / "sandbox"
    sandbox_dir.mkdir()
    return sandbox_dir


def test_geo_schema_injection_with_head(temp_sandbox) -> None:
    # Setup test file
    index_html = temp_sandbox / "index.html"
    index_html.write_text("""<!DOCTYPE html>
<html>
<head>
  <title>Test Page</title>
</head>
<body>
  <h1>Hello World</h1>
</body>
</html>
""")

    # Populate state
    target = TargetRef(
        source_type="local_path",
        value=str(temp_sandbox),
        resolved_at="2026-06-23T00:00:00Z"
    )
    audit = AuditResult(
        target=target,
        run_at="2026-06-23T00:00:00Z",
        findings=[],
        raw_json_path="/tmp/mock.json"
    )
    diag = DiagnosisItems(
        items=[
            DiagnosisItem(
                check_id="geo-schema-markup",
                severity="info",
                explanation="Missing GEO schema",
                remediation_type="geo_schema",
                proposed_action="Add JSON-LD"
            )
        ]
    )
    draft = RemediationDraft(
        geo_schema_draft='{"@context": "https://schema.org", "@type": "WebSite", "name": "Geo Test"}'
    )

    # Initialize agent & runner
    agent = RemediationExecuteAgent(name="remediation_execute_agent")
    session_service = InMemorySessionService()
    session = session_service.create_session_sync(
        user_id="test_user",
        app_name="test",
        state={
            "target": target.model_dump(),
            "audit_result": audit.model_dump(),
            "diagnosis_items": diag.model_dump(),
            "remediation_draft": draft.model_dump(),
            "confirmation_response": "yes",
            "waiting_for_confirmation": False,
        }
    )

    runner = Runner(agent=agent, session_service=session_service, app_name="test")
    message = types.Content(role="user", parts=[types.Part.from_text(text="yes")])
    list(runner.run(new_message=message, user_id="test_user", session_id=session.id))

    # Assert changes applied correctly
    html_content = index_html.read_text()
    soup = BeautifulSoup(html_content, "html.parser")
    script = soup.head.find("script", type="application/ld+json")
    assert script is not None
    assert "Geo Test" in script.string


def test_geo_schema_injection_no_head(temp_sandbox) -> None:
    # Setup test file without head tag
    index_html = temp_sandbox / "index.html"
    index_html.write_text("""<!DOCTYPE html>
<html>
<body>
  <h1>Hello World</h1>
</body>
</html>
""")

    target = TargetRef(
        source_type="local_path",
        value=str(temp_sandbox),
        resolved_at="2026-06-23T00:00:00Z"
    )
    audit = AuditResult(
        target=target,
        run_at="2026-06-23T00:00:00Z",
        findings=[],
        raw_json_path="/tmp/mock.json"
    )
    diag = DiagnosisItems(
        items=[
            DiagnosisItem(
                check_id="geo-schema-markup",
                severity="info",
                explanation="Missing GEO schema",
                remediation_type="geo_schema",
                proposed_action="Add JSON-LD"
            )
        ]
    )
    draft = RemediationDraft(
        geo_schema_draft='{"@context": "https://schema.org", "@type": "WebSite", "name": "Geo Test"}'
    )

    agent = RemediationExecuteAgent(name="remediation_execute_agent")
    session_service = InMemorySessionService()
    session = session_service.create_session_sync(
        user_id="test_user",
        app_name="test",
        state={
            "target": target.model_dump(),
            "audit_result": audit.model_dump(),
            "diagnosis_items": diag.model_dump(),
            "remediation_draft": draft.model_dump(),
            "confirmation_response": "yes",
            "waiting_for_confirmation": False,
        }
    )

    runner = Runner(agent=agent, session_service=session_service, app_name="test")
    message = types.Content(role="user", parts=[types.Part.from_text(text="yes")])
    list(runner.run(new_message=message, user_id="test_user", session_id=session.id))

    html_content = index_html.read_text()
    assert "application/ld+json" in html_content
    assert "Geo Test" in html_content


def test_aria_label_injection(temp_sandbox) -> None:
    # Setup HTML with a button lacking label
    index_html = temp_sandbox / "index.html"
    index_html.write_text("""<!DOCTYPE html>
<html>
<body>
  <button id="btn1">Submit</button>
</body>
</html>
""")

    target = TargetRef(
        source_type="local_path",
        value=str(temp_sandbox),
        resolved_at="2026-06-23T00:00:00Z"
    )
    audit = AuditResult(
        target=target,
        run_at="2026-06-23T00:00:00Z",
        findings=[],
        raw_json_path="/tmp/mock.json"
    )
    diag = DiagnosisItems(
        items=[
            DiagnosisItem(
                check_id="agent-accessibility-tree",
                severity="moderate",
                explanation="Accessibility issues",
                remediation_type="aria_labels",
                proposed_action="Add labels"
            )
        ]
    )
    draft = RemediationDraft(
        aria_suggestions=[
            AriaLabelSuggestion(
                file_path="index.html",
                selector="#btn1",
                element_snippet="<button id=\"btn1\">",
                aria_label="Submit Feedback"
            )
        ]
    )

    agent = RemediationExecuteAgent(name="remediation_execute_agent")
    session_service = InMemorySessionService()
    session = session_service.create_session_sync(
        user_id="test_user",
        app_name="test",
        state={
            "target": target.model_dump(),
            "audit_result": audit.model_dump(),
            "diagnosis_items": diag.model_dump(),
            "remediation_draft": draft.model_dump(),
            "confirmation_response": "yes",
            "waiting_for_confirmation": False,
        }
    )

    runner = Runner(agent=agent, session_service=session_service, app_name="test")
    message = types.Content(role="user", parts=[types.Part.from_text(text="yes")])
    list(runner.run(new_message=message, user_id="test_user", session_id=session.id))

    html_content = index_html.read_text()
    soup = BeautifulSoup(html_content, "html.parser")
    btn = soup.find("button", id="btn1")
    assert btn is not None
    assert btn.get("aria-label") == "Submit Feedback"


def test_aria_label_skipped_already_present(temp_sandbox) -> None:
    # Setup HTML with a button that already has the target label
    index_html = temp_sandbox / "index.html"
    index_html.write_text("""<!DOCTYPE html>
<html>
<body>
  <button id="btn1" aria-label="Submit Feedback">Submit</button>
</body>
</html>
""")

    target = TargetRef(
        source_type="local_path",
        value=str(temp_sandbox),
        resolved_at="2026-06-23T00:00:00Z"
    )
    audit = AuditResult(
        target=target,
        run_at="2026-06-23T00:00:00Z",
        findings=[],
        raw_json_path="/tmp/mock.json"
    )
    diag = DiagnosisItems(
        items=[
            DiagnosisItem(
                check_id="agent-accessibility-tree",
                severity="moderate",
                explanation="Accessibility issues",
                remediation_type="aria_labels",
                proposed_action="Add labels"
            )
        ]
    )
    draft = RemediationDraft(
        aria_suggestions=[
            AriaLabelSuggestion(
                file_path="index.html",
                selector="#btn1",
                element_snippet="<button id=\"btn1\"",
                aria_label="Submit Feedback"
            )
        ]
    )

    agent = RemediationExecuteAgent(name="remediation_execute_agent")
    session_service = InMemorySessionService()
    session = session_service.create_session_sync(
        user_id="test_user",
        app_name="test",
        state={
            "target": target.model_dump(),
            "audit_result": audit.model_dump(),
            "diagnosis_items": diag.model_dump(),
            "remediation_draft": draft.model_dump(),
            "confirmation_response": "yes",
            "waiting_for_confirmation": False,
        }
    )

    runner = Runner(agent=agent, session_service=session_service, app_name="test")
    message = types.Content(role="user", parts=[types.Part.from_text(text="yes")])
    list(runner.run(new_message=message, user_id="test_user", session_id=session.id))

    # Check results in state
    updated_session = session_service.get_session_sync(app_name="test", user_id="test_user", session_id=session.id)
    rem_res = updated_session.state.get("remediation_result")
    assert rem_res is not None
    actions = rem_res.get("actions", [])
    assert len(actions) > 0
    assert actions[0]["action_taken"] == "skipped_already_present"


def test_remediation_rejected(temp_sandbox) -> None:
    # Setup index.html
    index_html = temp_sandbox / "index.html"
    index_html.write_text("<html></html>")

    target = TargetRef(
        source_type="local_path",
        value=str(temp_sandbox),
        resolved_at="2026-06-23T00:00:00Z"
    )
    audit = AuditResult(
        target=target,
        run_at="2026-06-23T00:00:00Z",
        findings=[],
        raw_json_path="/tmp/mock.json"
    )
    diag = DiagnosisItems(
        items=[
            DiagnosisItem(
                check_id="llms-txt-exists",
                severity="critical",
                explanation="llms.txt missing",
                remediation_type="llms_txt",
                proposed_action="Create llms.txt"
            )
        ]
    )
    draft = RemediationDraft(
        llms_txt_content="Mock instructions"
    )

    agent = RemediationExecuteAgent(name="remediation_execute_agent")
    session_service = InMemorySessionService()
    session = session_service.create_session_sync(
        user_id="test_user",
        app_name="test",
        state={
            "target": target.model_dump(),
            "audit_result": audit.model_dump(),
            "diagnosis_items": diag.model_dump(),
            "remediation_draft": draft.model_dump(),
            "confirmation_response": "no",
            "waiting_for_confirmation": False,
        }
    )

    runner = Runner(agent=agent, session_service=session_service, app_name="test")
    message = types.Content(role="user", parts=[types.Part.from_text(text="no")])
    list(runner.run(new_message=message, user_id="test_user", session_id=session.id))

    # Assert no files written
    assert not (temp_sandbox / "llms.txt").exists()

    updated_session = session_service.get_session_sync(app_name="test", user_id="test_user", session_id=session.id)
    rem_res = updated_session.state.get("remediation_result")
    actions = rem_res.get("actions", [])
    assert actions[0]["action_taken"] == "skipped_user_rejected"
