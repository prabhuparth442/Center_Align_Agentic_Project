# Architecture (design; implemented in agent/, see _context/log for the current state)

Goal: take a natural-language office task, complete it in two web apps, and prove it was completed.
The loop is hand-written Python so every decision can be explained and debugged in a live walkthrough.

```
            goal (natural language)
                   |
                   v
   +---------------------------------+
   |  PLANNER   (thinking model)     |  goal + memory + failures -> short JSON plan
   +----------------+----------------+  (3-8 steps: intent, tool, expected result)
                    |
        +-----------v-----------+
        |  EXECUTOR (fast model)|  one step -> one tool call
        +-----------+-----------+
                    |  observation
        +-----------v-----------+      fail, after retries
        |  CHECKER  (code first)|----------------------------> back to PLANNER (replan / ask_human)
        +-----------+-----------+
                    | ok
                    v   (repeat until plan done)
        +-----------------------+
        |  VERIFIER (read-back) |  reads system state independently, compares with intent
        +-----------+-----------+
                    v
        finish(status, summary, evidence)      every step -> AUDIT TRACE (JSONL + readable report)
```

## Components

| Component | Responsibility | Uses an LLM? |
|---|---|---|
| Planner | turn the goal into steps; replan when a step fails or facts change; decide to ask the human | yes, thinking enabled |
| Executor | pick the arguments for one tool call from the step, the latest snapshot and memory | yes, thinking disabled |
| Checker | did the observation match `expected`? error code classification, retry/backoff, idempotency guard | code first, LLM only for fuzzy judgement |
| Verifier | read the system of record back (API + detail page) and compare every field with the grounded values | no (pure code) |
| Policy | permission layer: allowed origins, allowed write, approval gates, blocks | no |
| Memory | structured run facts with provenance (`value`, `source`, `quote`) | no |
| Trace | one JSON line per decision, tool call, observation, retry, approval; plus a markdown run report | no |
| Tools | see `TOOLS.md` | no |

## Control rules

- Retry budget per step: 2 retries for transient errors with backoff 1s then 2s; honour `Retry-After`.
- Replan budget per run: 2. After that the run ends `needs_human` with evidence, never a guess.
- Step timeout: 10s by default (a submit that exceeds it is *polled and verified*, not resubmitted).
- Token budget per run is configurable and logged; the run stops cleanly when exceeded.
- `finish(success)` is accepted only after the verifier passed.
- The model sees stable error codes and compact snapshots, never raw stack traces or credentials.

## Hallucination control

Extracted values must pass `grounded_check` (appear in the source text) before any write; the write is
done by a fixed tool; the result is read back and compared. A hallucinated amount fails grounding or
fails read-back, so it is caught twice.

## Mapping to the company's platform vocabulary

| centralign.ai term | Here |
|---|---|
| Reasoning Engine | Planner |
| Workflow Engine | the loop in `agent/loop.py` |
| Company Memory | run memory (facts with provenance) |
| Permission Layer | `policy.py` |
| Human Escalation | `ask_human`, `request_approval`, `needs_human` status |
| Tool Execution | `agent/tools/*` |
| Observability | audit trace + run report |

## Planned module layout

```
agent/
  config.py        settings from environment (present)
  llm.py           OpenAI-compatible client wrapper; thinking on/off per call; token accounting
  state.py         RunState: goal, plan, step index, memory, budgets, trace handle
  planner.py       plan / replan prompts + pydantic schema for the plan
  executor.py      single-step tool selection
  checker.py       expected-vs-observed, error classification, retry decisions
  verifier.py      read-back and field-by-field comparison
  policy.py        permission layer
  trace.py         JSONL audit trace + markdown run report
  tools/           browser.py  documents.py  finance_api.py  control.py
  loop.py          the loop
  cli.py           python -m agent.cli "goal"
eval/
  run_scenarios.py reset apps -> set faults -> run agent -> score -> table
scenarios/
  scenarios.json   25 scenarios (business traps, failure injection)
```

## Build order

1. Tools without an LLM; a script that completes the main task using only the tools (ground truth).
2. Trace and verifier.
3. Planner + executor + checker; run S1.
4. Policy, approvals, `ask_human`; run S2, S8-S12.
5. Reliability handling; run S3-S7, S13-S25.
6. Eval harness table, README results, demo.
