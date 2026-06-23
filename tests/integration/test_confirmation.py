# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import pytest
from typing import AsyncGenerator

from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.events import Event
from google.adk.agents.invocation_context import InvocationContext
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from workflows_sequential.agent import root_agent, _state_event, _text_event
from workflows_sequential.models import (
    AuditResult,
    LighthouseFinding,
    TargetRef,
    DiagnosisItems,
    DiagnosisItem,
    RemediationDraft,
    AriaLabelSuggestion,
)


async def mock_run_audit_shared(
    agent_name: str,
    ctx: InvocationContext,
    target: TargetRef,
    state_key: str,
    log_prefix: str,
) -> AsyncGenerator[Event, None]:
    # Construct a mock AuditResult
    findings = [
        LighthouseFinding(
            check_id="llms-txt-exists",
            applicable=True,
            passed=False,
            details="No llms.txt file found at site root",
            category="agentic_browsing",
        ),
        LighthouseFinding(
            check_id="agent-accessibility-tree",
            applicable=True,
            passed=False,
            details="Missing descriptive label on element",
            category="agentic_browsing",
            failing_nodes=[{"label": "button", "selector": "#btn1", "snippet": "<button id='btn1'>"}]
        ),
        LighthouseFinding(
            check_id="geo-schema-markup",
            applicable=True,
            passed=False,
            details="Missing JSON-LD schema markup",
            category="geo_readiness"
        )
    ]
    if state_key == "after_audit_result":
        # Simulate fixes applied successfully
        findings = [
            LighthouseFinding(
                check_id="llms-txt-exists",
                applicable=True,
                passed=True,
                details="llms.txt found",
                category="agentic_browsing",
            ),
            LighthouseFinding(
                check_id="agent-accessibility-tree",
                applicable=True,
                passed=True,
                details="descriptive label present",
                category="agentic_browsing",
            ),
            LighthouseFinding(
                check_id="geo-schema-markup",
                applicable=True,
                passed=True,
                details="JSON-LD schema markup detected",
                category="geo_readiness"
            )
        ]

    audit_result = AuditResult(
        target=target,
        run_at="2026-06-23T00:00:00Z",
        findings=findings,
        raw_json_path="/tmp/mock_lighthouse.json",
    )
    yield _state_event(agent_name, ctx, {state_key: audit_result.model_dump()})
    yield _text_event(agent_name, ctx, f"[{log_prefix}] Mock complete")


async def mock_skippable_llm_run(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
    if ctx.session.state.get("confirmation_response") is not None:
        return
    items = [
        DiagnosisItem(
            check_id="llms-txt-exists",
            severity="critical",
            explanation="llms.txt is required for AI crawlers.",
            remediation_type="llms_txt",
            proposed_action="Create llms.txt at root",
        ),
        DiagnosisItem(
            check_id="agent-accessibility-tree",
            severity="moderate",
            explanation="ARIA labels are required for screen readers.",
            remediation_type="aria_labels",
            proposed_action="Add aria-labels to buttons",
        ),
        DiagnosisItem(
            check_id="geo-schema-markup",
            severity="info",
            explanation="GEO schema markup is required for citation optimization.",
            remediation_type="geo_schema",
            proposed_action="Add JSON-LD",
        )
    ]
    diag = DiagnosisItems(items=items)
    yield _state_event(self.name, ctx, {self.output_key: diag.model_dump()})


async def mock_remediation_draft_run(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
    if ctx.session.state.get("confirmation_response") is not None:
        return
    draft = RemediationDraft(
        llms_txt_content="Mock llms.txt content",
        aria_suggestions=[
            AriaLabelSuggestion(
                file_path="index.html",
                selector="#btn1",
                element_snippet="<button id='btn1'>",
                aria_label="Test Button"
            )
        ],
        geo_schema_draft='{"@context": "https://schema.org", "@type": "WebSite", "name": "Mock Test Site"}'
    )
    yield _state_event(self.name, ctx, {self.output_key: draft.model_dump()})


@pytest.fixture
def temp_sandbox(tmp_path) -> os.PathLike:
    sandbox_dir = tmp_path / "sandbox"
    sandbox_dir.mkdir()
    index_html = sandbox_dir / "index.html"
    index_html.write_text("""<!DOCTYPE html>
<html>
<head>
  <title>Test Page</title>
</head>
<body>
  <button id="btn1">Click me</button>
</body>
</html>
""")
    return sandbox_dir


def test_confirmation_flow_yes(temp_sandbox, monkeypatch) -> None:
    # Monkeypatch the remote/CLI steps to keep test fast and stable
    monkeypatch.setattr("workflows_sequential.agent._run_audit_shared", mock_run_audit_shared)
    monkeypatch.setattr("workflows_sequential.agent.SkippableLlmAgent._run_async_impl", mock_skippable_llm_run)
    monkeypatch.setattr("workflows_sequential.agent.RemediationDraftAgent._run_async_impl", mock_remediation_draft_run)

    session_service = InMemorySessionService()
    session = session_service.create_session_sync(user_id="test_user", app_name="test")
    runner = Runner(agent=root_agent, session_service=session_service, app_name="test")

    # --- Turn 1: Initial audit command ---
    message_turn1 = types.Content(
        role="user", parts=[types.Part.from_text(text=f"--path {temp_sandbox}")]
    )

    events_turn1 = list(
        runner.run(
            new_message=message_turn1,
            user_id="test_user",
            session_id=session.id,
            run_config=RunConfig(streaming_mode=StreamingMode.SSE),
        )
    )

    assert len(events_turn1) > 0
    session_state = session_service.get_session_sync(app_name="test", user_id="test_user", session_id=session.id).state
    assert session_state.get("waiting_for_confirmation") is True
    assert session_state.get("confirmation_response") is None

    # Check for proposed changes box in turn 1 response
    has_proposal_box = False
    for event in events_turn1:
        if event.content and event.content.parts:
            text = event.content.parts[0].text or ""
            if "PROPOSED CHANGES" in text:
                has_proposal_box = True
                break
    assert has_proposal_box, "Expected to find proposed changes box in turn 1 response"

    # Verify no files are written yet
    assert not (temp_sandbox / "llms.txt").exists()

    # --- Turn 2: User responds with "yes" ---
    message_turn2 = types.Content(
        role="user", parts=[types.Part.from_text(text="yes")]
    )

    events_turn2 = list(
        runner.run(
            new_message=message_turn2,
            user_id="test_user",
            session_id=session.id,
            run_config=RunConfig(streaming_mode=StreamingMode.SSE),
        )
    )

    assert len(events_turn2) > 0
    session_state_after = session_service.get_session_sync(app_name="test", user_id="test_user", session_id=session.id).state
    assert session_state_after.get("waiting_for_confirmation") is False
    assert session_state_after.get("confirmation_response") is None

    # Files should be written/modified
    assert (temp_sandbox / "llms.txt").exists()
    assert (temp_sandbox / "llms.txt").read_text() == "Mock llms.txt content"

    index_content = (temp_sandbox / "index.html").read_text()
    assert "aria-label=\"Test Button\"" in index_content
    assert "application/ld+json" in index_content


def test_confirmation_flow_no(temp_sandbox, monkeypatch) -> None:
    monkeypatch.setattr("workflows_sequential.agent._run_audit_shared", mock_run_audit_shared)
    monkeypatch.setattr("workflows_sequential.agent.SkippableLlmAgent._run_async_impl", mock_skippable_llm_run)
    monkeypatch.setattr("workflows_sequential.agent.RemediationDraftAgent._run_async_impl", mock_remediation_draft_run)

    session_service = InMemorySessionService()
    session = session_service.create_session_sync(user_id="test_user", app_name="test")
    runner = Runner(agent=root_agent, session_service=session_service, app_name="test")

    # --- Turn 1: Initial audit command ---
    message_turn1 = types.Content(
        role="user", parts=[types.Part.from_text(text=f"--path {temp_sandbox}")]
    )

    events_turn1 = list(
        runner.run(
            new_message=message_turn1,
            user_id="test_user",
            session_id=session.id,
            run_config=RunConfig(streaming_mode=StreamingMode.SSE),
        )
    )

    session_state = session_service.get_session_sync(app_name="test", user_id="test_user", session_id=session.id).state
    assert session_state.get("waiting_for_confirmation") is True

    # --- Turn 2: User responds with "no" ---
    message_turn2 = types.Content(
        role="user", parts=[types.Part.from_text(text="no")]
    )

    events_turn2 = list(
        runner.run(
            new_message=message_turn2,
            user_id="test_user",
            session_id=session.id,
            run_config=RunConfig(streaming_mode=StreamingMode.SSE),
        )
    )

    assert len(events_turn2) > 0
    session_state_after = session_service.get_session_sync(app_name="test", user_id="test_user", session_id=session.id).state
    assert session_state_after.get("waiting_for_confirmation") is False
    assert session_state_after.get("confirmation_response") is None

    # Files should NOT be written/modified
    assert not (temp_sandbox / "llms.txt").exists()
    index_content = (temp_sandbox / "index.html").read_text()
    assert "aria-label=\"Test Button\"" not in index_content
    assert "application/ld+json" not in index_content

    # The remediation actions should be skipped_user_rejected
    remediation_res = session_state_after.get("remediation_result")
    assert remediation_res is not None
    actions = remediation_res.get("actions")
    assert len(actions) > 0
    for act in actions:
        assert act.get("action_taken") == "skipped_user_rejected"


def test_confirmation_resilience_new_target(temp_sandbox, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("workflows_sequential.agent._run_audit_shared", mock_run_audit_shared)
    monkeypatch.setattr("workflows_sequential.agent.SkippableLlmAgent._run_async_impl", mock_skippable_llm_run)
    monkeypatch.setattr("workflows_sequential.agent.RemediationDraftAgent._run_async_impl", mock_remediation_draft_run)

    session_service = InMemorySessionService()
    session = session_service.create_session_sync(user_id="test_user", app_name="test")
    runner = Runner(agent=root_agent, session_service=session_service, app_name="test")

    # --- Turn 1: Initial audit command ---
    message_turn1 = types.Content(
        role="user", parts=[types.Part.from_text(text=f"--path {temp_sandbox}")]
    )

    list(
        runner.run(
            new_message=message_turn1,
            user_id="test_user",
            session_id=session.id,
            run_config=RunConfig(streaming_mode=StreamingMode.SSE),
        )
    )

    session_state = session_service.get_session_sync(app_name="test", user_id="test_user", session_id=session.id).state
    assert session_state.get("waiting_for_confirmation") is True

    # --- Turn 2: User responds with a new audit command instead of yes/no ---
    other_sandbox = tmp_path / "other_sandbox"
    other_sandbox.mkdir()
    other_index = other_sandbox / "index.html"
    other_index.write_text("<html></html>")

    message_turn2 = types.Content(
        role="user", parts=[types.Part.from_text(text=f"--path {other_sandbox}")]
    )

    events_turn2 = list(
        runner.run(
            new_message=message_turn2,
            user_id="test_user",
            session_id=session.id,
            run_config=RunConfig(streaming_mode=StreamingMode.SSE),
        )
    )

    # It should have started a fresh audit, meaning it returns early waiting for confirmation on the new path
    assert len(events_turn2) > 0
    session_state_after = session_service.get_session_sync(app_name="test", user_id="test_user", session_id=session.id).state
    assert session_state_after.get("waiting_for_confirmation") is True
    target = session_state_after.get("target")
    assert target is not None
    assert target.get("value") == str(other_sandbox)
