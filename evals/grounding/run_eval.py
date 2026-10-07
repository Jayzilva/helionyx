"""Capture grounding-evaluation transcripts automatically (SRS §9.5).

Runs every prompt in prompts.yaml through Claude with the Helionyx skill as the
system prompt and the Helionyx MCP tools (served in-process), then writes one
transcript per prompt in the format read by ``helionyx eval grounding``.

Usage (needs Anthropic credentials: ANTHROPIC_API_KEY or `ant auth login`):

    pip install -e ".[eval]"
    python evals/grounding/run_eval.py --out evals/grounding/transcripts
    helionyx eval grounding evals/grounding/transcripts

Every run spends real API tokens: 30 prompts × several tool turns each. Only Sonnet
models are allowed, each response is capped by --max-tokens, and the whole run stops
once input plus output tokens reach --budget-tokens.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path
from typing import Any

import anthropic
import yaml
from mcp import Client

from helionyx.api.mcp_server import build_server

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
MODELS = ("claude-sonnet-5-5", "claude-sonnet-5")
MAX_TURNS = 12


def skill_prompt() -> str:
    text = (ROOT / "skills" / "helionyx" / "SKILL.md").read_text(encoding="utf-8")
    return re.sub(r"^---.*?---\s*", "", text, flags=re.DOTALL)


class Budget:
    """Running token count for the whole evaluation; cached input counts at full size."""

    def __init__(self, limit: int) -> None:
        self.limit, self.input, self.output = limit, 0, 0

    def add(self, usage: Any) -> None:
        self.input += (usage.input_tokens + (usage.cache_read_input_tokens or 0)
                       + (usage.cache_creation_input_tokens or 0))
        self.output += usage.output_tokens

    @property
    def spent(self) -> int:
        return self.input + self.output

    @property
    def exhausted(self) -> bool:
        return self.spent >= self.limit


async def run_prompt(client: anthropic.AsyncAnthropic, mcp: Client, tools: list[dict[str, Any]],
                     prompt: dict[str, Any], model: str, effort: str, max_tokens: int,
                     budget: Budget) -> dict[str, Any]:
    transcript: list[dict[str, Any]] = [{"role": "user", "text": prompt["text"]}]
    messages: list[dict[str, Any]] = [{"role": "user", "content": prompt["text"]}]
    for _ in range(MAX_TURNS):
        if budget.exhausted:
            transcript.append({"role": "assistant", "text": "[token budget exhausted]"})
            break
        # No refusal fallbacks: they could route to a non-Sonnet model.
        response = await client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=skill_prompt(),
            tools=tools,
            messages=messages,
            output_config={"effort": effort},
            cache_control={"type": "ephemeral"},
        )
        budget.add(response.usage)
        text = "".join(b.text for b in response.content if b.type == "text")
        if text:
            transcript.append({"role": "assistant", "text": text})
        if response.stop_reason == "refusal":
            transcript.append({"role": "assistant", "text": "[refused]"})
            break
        if response.stop_reason == "max_tokens":
            transcript.append({"role": "assistant", "text": "[max_tokens reached]"})
            break
        if response.stop_reason != "tool_use":
            break
        # Append the full content so thinking blocks are replayed unchanged.
        messages.append({"role": "assistant", "content": response.content})
        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            r = await mcp.call_tool(block.name, block.input)
            structured = r.structured_content or {}
            transcript.append({"role": "tool", "name": block.name, "result": structured})
            results.append({"type": "tool_result", "tool_use_id": block.id, "is_error": bool(r.is_error),
                            "content": json.dumps(structured) if structured else
                            "".join(getattr(c, "text", "") for c in r.content)})
        messages.append({"role": "user", "content": results})
    return {"id": prompt["id"], "persona": prompt.get("persona"), "adversarial": bool(prompt.get("adversarial")),
            "model": model, "messages": transcript}


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", type=Path, default=HERE / "transcripts")
    ap.add_argument("--only", nargs="*", help="prompt IDs to run (default: all)")
    ap.add_argument("--model", default=MODELS[0], choices=MODELS)
    ap.add_argument("--effort", default="medium", choices=["low", "medium", "high", "xhigh", "max"])
    ap.add_argument("--max-tokens", type=int, default=4096, help="cap per response")
    ap.add_argument("--budget-tokens", type=int, default=2_000_000,
                    help="stop the run once input + output tokens reach this total")
    args = ap.parse_args()
    prompts = yaml.safe_load((HERE / "prompts.yaml").read_text(encoding="utf-8"))["prompts"]
    if args.only:
        prompts = [p for p in prompts if p["id"] in set(args.only)]
    args.out.mkdir(parents=True, exist_ok=True)
    client = anthropic.AsyncAnthropic()
    budget = Budget(args.budget_tokens)
    async with Client(build_server()) as mcp:
        tools = [{"name": t.name, "description": t.description or "", "input_schema": t.input_schema}
                 for t in (await mcp.list_tools()).tools]
        for p in prompts:
            if budget.exhausted:
                print(f"Token budget of {args.budget_tokens:,} reached; skipping the remaining prompts.")
                break
            t = await run_prompt(client, mcp, tools, p, args.model, args.effort, args.max_tokens, budget)
            (args.out / f"{p['id']}.json").write_text(json.dumps(t, indent=1, default=str), encoding="utf-8")
            print(f"{p['id']}: {sum(1 for m in t['messages'] if m['role'] == 'tool')} tool calls; "
                  f"{budget.spent:,} tokens used ({budget.input:,} in, {budget.output:,} out)")


if __name__ == "__main__":
    asyncio.run(main())
