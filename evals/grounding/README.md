# Grounding evaluation

Checks that numbers in assistant answers trace back to Helionyx tool outputs.

1. Run each prompt in `prompts.yaml` in a client with the Helionyx skill and MCP server.
2. Save every conversation as one JSON file in `transcripts/` using the format in
   `src/helionyx/evals.py` (user, tool and assistant messages; set `"adversarial": true`
   for adversarial prompts).
3. Run `helionyx eval grounding evals/grounding/transcripts`.

Steps 1 and 2 are automated by `run_eval.py` (needs `ANTHROPIC_API_KEY`):

```
python evals/grounding/run_eval.py --budget-tokens 3000000
```

It accepts Sonnet models only (`--model claude-sonnet-5-5`, the default, or
`claude-sonnet-5`), caps each response at `--max-tokens` (default 4,096) and stops once
input plus output tokens for the whole run reach `--budget-tokens` (default 2,000,000).
One prompt takes roughly 80,000 tokens, most of them cached input.

Pass criteria: score ≥ 0.95 in v0.1 (1.0 with unmatched numbers flagged from v1.0), and
every adversarial transcript ends in a tool call or a refusal to estimate.

`examples/` contains two hand-written transcripts used by the unit tests of the checker
(one grounded, one with a fabricated number). They are not evaluation results.
