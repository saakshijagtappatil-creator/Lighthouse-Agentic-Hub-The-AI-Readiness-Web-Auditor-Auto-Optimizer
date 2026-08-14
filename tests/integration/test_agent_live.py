import os
import pytest

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from workflows_sequential.agent import root_agent


@pytest.fixture
def temp_sandbox(tmp_path) -> os.PathLike:
    sandbox_dir = tmp_path / "sandbox"
    sandbox_dir.mkdir()
    # Write a simple page lacking aria-label on button and lacking JSON-LD schema
    index_html = sandbox_dir / "index.html"
    index_html.write_text("""<!DOCTYPE html>
<html>
<head>
  <title>Sandbox Page</title>
</head>
<body>
  <button id="submit-btn">Click Me</button>
</body>
</html>
""")
    return sandbox_dir


has_api_key = os.environ.get("GOOGLE_API_KEY") is not None


@pytest.mark.skipif(not has_api_key, reason="No GOOGLE_API_KEY environment variable found.")
def test_live_agent_audit(temp_sandbox) -> None:
    session_service = InMemorySessionService()
    session = session_service.create_session_sync(user_id="test_user", app_name="test")
    runner = Runner(agent=root_agent, session_service=session_service, app_name="test")

    # Turn 1: Initial audit command
    message_turn1 = types.Content(
        role="user", parts=[types.Part.from_text(text=f"--path {temp_sandbox}")]
    )

    events = list(
        runner.run(
            new_message=message_turn1,
            user_id="test_user",
            session_id=session.id,
        )
    )

    # Check output events
    assert len(events) > 0

    # Retrieve updated session state
    session_state = session_service.get_session_sync(
        app_name="test", user_id="test_user", session_id=session.id
    ).state

    # Verify Intake, Audit, Diagnosis, and Remediation Drafting completed
    assert session_state.get("target") is not None
    assert session_state.get("audit_result") is not None
    assert session_state.get("diagnosis_items") is not None
    assert session_state.get("remediation_draft") is not None

    # Verify that the flow is now waiting at the confirmation gate
    assert session_state.get("waiting_for_confirmation") is True

    # Assert that proposal changes box was generated in response
    has_proposal_box = False
    for event in events:
        if event.content and event.content.parts:
            text = event.content.parts[0].text or ""
            if "PROPOSED CHANGES" in text:
                has_proposal_box = True
                break
    assert has_proposal_box, "Expected to find proposed changes confirmation box in output"
