"""Build the Helionyx MCPB bundles (maintainers).

Writes two bundles to ``mcpb/``:

- ``helionyx-<version>.mcpb``: uv runtime, for Claude Desktop (the host installs helionyx from PyPI).
- ``helionyx-<version>-smithery.mcpb``: python runtime started with ``uvx``, with each tool's
  ``inputSchema`` added after packing. Smithery cannot read the uv runtime and needs the schemas,
  which the ``mcpb`` packer rejects, so the manifest inside the archive is patched.

Usage: python scripts/build_mcpb.py   (needs Node.js for ``npx @anthropic-ai/mcpb``)
Publish to Smithery: npx @smithery/cli mcp publish mcpb/helionyx-<version>-smithery.mcpb -n gitdevjay/helionyx
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from mcp import Client

from helionyx.api.mcp_server import build_server

ROOT = Path(__file__).resolve().parent.parent
BUNDLE = ROOT / "mcpb"


async def _tools() -> list[dict]:
    async with Client(build_server()) as c:
        return [{"name": t.name, "description": " ".join((t.description or "").split())[:200],
                 "inputSchema": t.input_schema} for t in (await c.list_tools()).tools]


def _pack(src: Path, out: Path) -> None:
    subprocess.run(["npx", "-y", "@anthropic-ai/mcpb", "pack", str(src), str(out)], check=True, shell=True)


def main() -> None:
    manifest = json.loads((BUNDLE / "manifest.json").read_text(encoding="utf-8"))
    version = manifest["version"]
    tools = asyncio.run(_tools())
    _pack(BUNDLE, BUNDLE / f"helionyx-{version}.mcpb")

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "smithery"
        shutil.copytree(BUNDLE, work, ignore=shutil.ignore_patterns("*.mcpb", ".venv", "uv.lock"))
        m = dict(manifest, manifest_version="0.3")
        m["server"] = {"type": "python", "entry_point": "src/server.py", "mcp_config": {
            "command": "uvx", "args": ["--from", f"helionyx=={version}", "helionyx", "serve"],
            "env": manifest["server"]["mcp_config"]["env"]}}
        m["compatibility"] = dict(manifest["compatibility"], runtimes={"python": ">=3.11"})
        m["tools"] = [{"name": t["name"], "description": t["description"]} for t in tools]
        (work / "manifest.json").write_text(json.dumps(m, indent=2, ensure_ascii=False), encoding="utf-8")
        base = Path(tmp) / "base.mcpb"
        _pack(work, base)
        out = BUNDLE / f"helionyx-{version}-smithery.mcpb"
        with zipfile.ZipFile(base) as zin, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename == "manifest.json":
                    m["tools"] = tools
                    data = json.dumps(m, indent=2, ensure_ascii=False).encode()
                zout.writestr(item, data)
    print(f"Wrote {BUNDLE / f'helionyx-{version}.mcpb'} and {out}")


if __name__ == "__main__":
    main()
