"""HTTP host: health check and bearer-key authentication (IF-MCP-02, AT-10 dev variant)."""

from __future__ import annotations

from starlette.testclient import TestClient

from helionyx.api.http_app import create_app

INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}}
HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def test_healthz_open_and_mcp_requires_key():
    with TestClient(create_app(api_key="s3cret"), base_url="http://127.0.0.1:8080") as c:
        assert c.get("/healthz").json()["status"] == "ok"
        r = c.post("/mcp", json=INIT, headers=HEADERS)
        assert r.status_code == 401 and r.json()["error"]["code"] == "HNX-E009"
        r = c.post("/mcp", json=INIT, headers={**HEADERS, "Authorization": "Bearer wrong"})
        assert r.status_code == 401
        r = c.post("/mcp", json=INIT, headers={**HEADERS, "Authorization": "Bearer s3cret"})
        assert r.status_code == 200 and "capabilities" in r.text
