"""Streamable HTTP host (IF-MCP-02): MCP at ``/mcp`` plus ``/healthz``.

Authentication before v1.0 is a static bearer key (``HNX_API_KEY``), which the SRS
allows for development only. OAuth 2.1 with Microsoft Entra ID (IF-MCP-08) replaces it
in hosted mode. Without a key the server must stay on localhost (enforced by the CLI).
"""

from __future__ import annotations

import hmac
import os

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from helionyx import __version__
from helionyx.api.mcp_server import build_server


class BearerKeyMiddleware:
    """Reject requests to anything but ``/healthz`` without ``Authorization: Bearer <key>``."""

    def __init__(self, app: ASGIApp, key: str) -> None:
        self.app = app
        self.key = key.encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope.get("path") != "/healthz":
            headers = dict(scope.get("headers") or [])
            auth = headers.get(b"authorization", b"")
            token = auth[7:] if auth[:7].lower() == b"bearer " else b""
            if not token or not hmac.compare_digest(token, self.key):
                body = {"error": {"code": "HNX-E009", "name": "UNAUTHORIZED",
                                  "message": "Missing or invalid bearer token.",
                                  "hint": "Send 'Authorization: Bearer <HNX_API_KEY>'."}}
                resp = JSONResponse(body, status_code=401, headers={"WWW-Authenticate": "Bearer"})
                await resp(scope, receive, send)
                return
        await self.app(scope, receive, send)


def create_app(host: str = "127.0.0.1", api_key: str | None = None) -> ASGIApp:
    mcp = build_server()

    @mcp.custom_route("/healthz", methods=["GET"])
    async def healthz(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "name": "helionyx", "version": __version__})

    app: Starlette = mcp.streamable_http_app(streamable_http_path="/mcp", host=host)
    key = api_key if api_key is not None else os.environ.get("HNX_API_KEY")
    return BearerKeyMiddleware(app, key) if key else app
