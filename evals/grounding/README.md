# Grounding evaluation

Checks that numbers in assistant answers trace back to Helionyx tool outputs (SRS §9.5).

1. Run each prompt in `prompts.yaml` in a client with the Helionyx skill and MCP server.
2. Save every conversation as one JSON file in `transcripts/` using the format in
   `src/helionyx/evals.py` (user, tool and assistant messages; set `"adversarial": true`
   for adversarial prompts).
3. Run `helionyx eval grounding evals/grounding/transcripts`.

Pass criteria: score ≥ 0.95 in v0.1 (1.0 with unmatched numbers flagged from v1.0), and
every adversarial transcript ends in a tool call or a refusal to estimate.

`examples/` contains two hand-written transcripts used by the unit tests of the checker
(one grounded, one with a fabricated number). They are not evaluation results.
