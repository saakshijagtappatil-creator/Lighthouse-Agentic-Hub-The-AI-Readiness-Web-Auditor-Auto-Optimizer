import pytest
import os
from workflows_sequential.mcp_server import audit_web_readiness

@pytest.mark.anyio
async def test_mcp_path_traversal_guard() -> None:
    """Verifies that the MCP server rejects directories outside the workspace."""
    with pytest.raises(ValueError, match="Security Error: Audit target path"):
        await audit_web_readiness("/etc")

@pytest.mark.anyio
async def test_mcp_path_exists_validation() -> None:
    """Verifies that the MCP server rejects local paths that do not exist."""
    # A path that doesn't exist but is within the workspace
    fake_path = os.path.join(os.getcwd(), "nonexistent_directory_123")
    with pytest.raises(ValueError, match="Path Error: Target path"):
        await audit_web_readiness(fake_path)

@pytest.mark.anyio
async def test_mcp_url_validation() -> None:
    """Verifies that the MCP server rejects invalid URL formats."""
    # ftp scheme is treated as path and fails existence check
    with pytest.raises(ValueError, match="Path Error: Target path"):
        await audit_web_readiness("ftp://example.com")
