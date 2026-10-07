"""MCPB entry point: run the Helionyx MCP server over stdio."""

from helionyx.api.mcp_server import mcp

if __name__ == "__main__":
    mcp.run("stdio")
