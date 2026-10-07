"""Streamable HTTP host (IF-MCP-02): MCP at ``/mcp`` plus ``/healthz``.

v0.1 has **no authentication** (OAuth 2.1 with Entra ID arrives in v1.0, IF-MCP-08),
so the server binds to 127.0.0.1 by default. Do not expose it publicly.
"""

from __future__ import annotations

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse

from helionyx import __version__
from helionyx.api.mcp_server import build_server


def create_app(host: str = "127.0.0.1") -> Starlette:
    mcp = build_server()

    @mcp.custom_route("/healthz", methods=["GET"])
    async def healthz(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "name": "helionyx", "version": __version__})

    return mcp.streamable_http_app(streamable_http_path="/mcp", host=host)
