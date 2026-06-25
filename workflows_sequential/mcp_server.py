"""
Lighthouse Agentic Hub: Hardened Model Context Protocol (MCP) Server.
Exposes the AI-Readiness Auditor to external LLM clients safely and securely.
"""

import os
import sys
import uuid
import urllib.parse
import asyncio
from functools import partial
from mcp.server.fastmcp import FastMCP

from workflows_sequential.agent import _run_audit_shared
from workflows_sequential.models import TargetRef

# Initialize FastMCP Server
mcp = FastMCP("Lighthouse Agentic Hub Auditor")

# Mock Context to satisfy _run_audit_shared contract
class MockSession:
    def __init__(self):
        self.state = {}

class MockContext:
    def __init__(self, invocation_id: str):
        self.invocation_id = invocation_id
        self.session = MockSession()
        self.state = self.session.state

@mcp.tool()
async def audit_web_readiness(target: str) -> str:
    """
    Audits a local workspace directory or live website URL for AI Agent readiness
    (Accessibility, Performance, and Agentic Browsing).
    
    Args:
        target: The absolute or relative path to a local directory, or a public HTTP/HTTPS URL.
    
    Returns:
        A Markdown-formatted report summarizing findings, scores, and failing element nodes.
    """
    is_url = target.startswith(("http://", "https://"))
    
    # 1. Path Traversal Guard & Target Validation
    if is_url:
        try:
            parsed = urllib.parse.urlparse(target)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                raise ValueError("Target URL has an invalid scheme or format (must be http/https).")
        except Exception as e:
            raise ValueError(f"Security Error: Invalid URL target: {e}")
    else:
        abs_target = os.path.realpath(target)
        workspace_root = os.path.abspath(os.getcwd())
        
        # Enforce that the directory lies inside the workspace
        if not (abs_target == workspace_root or abs_target.startswith(workspace_root + os.sep)):
            raise ValueError(
                f"Security Error: Audit target path '{target}' lies outside the permitted workspace root: '{workspace_root}'"
            )
            
        if not os.path.exists(abs_target):
            raise ValueError(f"Path Error: Target path '{target}' does not exist.")
        if not os.path.isdir(abs_target):
            raise ValueError(f"Path Error: Target path '{target}' is not a directory.")

    # 2. Setup mock invocation context
    inv_id = f"mcp-audit-{uuid.uuid4().hex}"
    ctx = MockContext(invocation_id=inv_id)
    
    from datetime import datetime, timezone
    source_type = "url" if is_url else "local_path"
    target_ref = TargetRef(
        source_type=source_type,
        value=target,
        resolved_at=datetime.now(timezone.utc).isoformat()
    )
    
    # 3. Execute audit in a thread pool to avoid blocking the async event loop
    audit_result_dict = None
    
    try:
        async for event in _run_audit_shared(
            agent_name="mcp_server",
            ctx=ctx,
            target=target_ref,
            state_key="mcp_audit",
            log_prefix="MCP_AUDIT",
        ):
            # Capture the state update event
            if event.actions and event.actions.state_delta and "mcp_audit" in event.actions.state_delta:
                audit_result_dict = event.actions.state_delta["mcp_audit"]
    except Exception as e:
        return f"### Audit Failed\nAn error occurred while executing the Lighthouse audit: {e}"

    if not audit_result_dict:
        return "### Audit Failed\nAudit completed but failed to yield any structured findings."

    # 4. Format structured Markdown report to return to the calling agent
    findings = audit_result_dict.get("findings", [])
    
    categories = {
        "agentic_browsing": [],
        "accessibility": [],
        "performance": [],
    }
    
    for f in findings:
        cat = f.get("category", "agentic_browsing")
        if cat in categories:
            categories[cat].append(f)

    report_parts = [
        f"# AI Readiness Audit Report",
        f"**Target**: `{target}`",
        f"**Run ID**: `{inv_id}`",
        "",
        "## Summary",
    ]
    
    total_failed = sum(1 for f in findings if not f.get("passed") and f.get("applicable"))
    total_passed = sum(1 for f in findings if f.get("passed") and f.get("applicable"))
    
    report_parts.append(f"- **Passed Checks**: {total_passed}")
    report_parts.append(f"- **Failed Checks**: {total_failed}")
    report_parts.append("")

    for cat_name, cat_title in [
        ("agentic_browsing", "Agentic Browsing"),
        ("accessibility", "Accessibility (Diagnosis)"),
        ("performance", "Performance (Diagnosis)")
    ]:
        report_parts.append(f"## {cat_title}")
        cat_findings = categories[cat_name]
        
        # Only list failing checks for brevity, or a summary line if all passed
        failed_items = [f for f in cat_findings if not f.get("passed") and f.get("applicable")]
        if not failed_items:
            report_parts.append("✅ All checks in this category passed successfully.")
            report_parts.append("")
            continue
            
        for f in failed_items:
            report_parts.append(f"❌ **{f.get('check_id')}**: {f.get('details')}")
            # List failing nodes if available
            nodes = f.get("failing_nodes", [])
            if nodes:
                report_parts.append("   *Affected Elements:*")
                for n in nodes[:3]:  # Limit to top 3 elements
                    report_parts.append(f"   - Selector: `{n.get('selector')}`")
                    snippet = n.get('snippet', '').strip().replace('\n', ' ')
                    if snippet:
                        report_parts.append(f"     HTML: `{snippet[:100]}`")
        report_parts.append("")

    return "\n".join(report_parts)

if __name__ == "__main__":
    mcp.run()
