# Autonomous AI Task Worker (CentrAlign AI intern project)

An AI worker that takes a plain-language office task, operates company web apps through a browser,
verifies that the work was really done, and reports evidence. It is being built against a small
simulated company environment (a mail portal and a finance system) so every run is repeatable and
failures can be injected on demand.

> **Status: environment and agent built; evaluation not done yet.** The table below says what exists.
> This README does not claim results that have not been produced.

| Part | State |
|---|---|
| Mock Mail & Documents portal (`Website_1`, port 5001) | built, 25 tests passing |
| Mock Finance system (`Website_2`, port 5002) | built, 44 tests passing |
| Failure injection (19 flags across both apps) | built and tested, see `docs/FAILURE_INJECTION.md` |
| Scenario matrix (25 scenarios) | written, `scenarios/scenarios.json` |
| Tool plan, permission layer, architecture | designed, see `docs/TOOLS.md`, `docs/ARCHITECTURE.md` |
| Agent (planner, executor, checker, policy, verifier, 17 tools, terminal front end) | built, runs end to end; tested with a scripted stand-in model against the real apps, and a few runs with the real model |
| Evaluation harness and results | **not implemented** (`eval/run_scenarios.py` is a stub), no results table yet |
| Demo video | not recorded |

## The task

> "Find the latest invoice from Company X Ltd in the inbox, enter it into the finance system, and
> confirm when it is done."

The data is full of realistic traps: a duplicate resend, a credit note that looks like the newest
invoice, a similarly named vendor, an amount that appears twice with different values, a missing due
date, a vendor that does not exist in the finance system, and a past-due invoice the form rejects.

## Design in one paragraph

A hand-written loop: a **planner** (thinking model) makes a short plan, an **executor** (fast model)
performs one tool call per step, a **checker** (code first) decides whether the observation matches
what was expected, and a **verifier** reads the finance system back independently before the run may
claim success. A **permission layer** in code (not in the prompt) limits what the agent can do and
requires approval or a human answer for risky or ambiguous cases. Every decision is written to an
audit trace. Details: `docs/ARCHITECTURE.md`.

## Repository layout

```
Website_1/            mock mail portal (Flask + SQLite), its SPEC.md, tests
Website_2/            mock finance system (Flask + SQLite), its SPEC.md, tests
agent/                the agent: loop, planner, executor, llm, checker, policy, verifier, state, trace, config, cli
agent/tools/          browser (Playwright), documents (PDF), finance_api (read-only), control (memory, approval, finish)
runs/                 one folder per run: report.md (answer on top), trace.jsonl, downloads/
docs/                 ARCHITECTURE.md, TOOLS.md, FAILURE_INJECTION.md
scenarios/            scenarios.json
_context/             working memory: INDEX.md, project-context.md, log/ (decisions)
```

## Setup

Mock apps (separate virtual environments are fine; each folder has its own requirements):

```bash
cd Website_1
python -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/python app.py            # http://127.0.0.1:5001   login demo_user / demo123
.venv/bin/python -m pytest -q

cd ../Website_2
python -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/python app.py            # http://127.0.0.1:5002   login finance_user / finance123
.venv/bin/python -m pytest -q
```

(Windows: use `.venv\Scripts\python` instead of `.venv/bin/python`.) Both apps also take `FAULTS`,
`PORT` and `APP_TODAY` environment variables; see each folder's README.

Agent (start both mock apps first):

```bash
pip install -r requirements.txt && playwright install chromium
# .env needs LLM_BASE_URL, LLM_API_KEY, PLANNER_MODEL (never commit it)
python -m agent.cli "Find the latest invoice from Company X Ltd ..."   # live output, Ctrl+C pauses
python -m agent.cli                # no goal on a terminal: asks for goals one after another
HEADLESS=0 SLOW_MO_MS=500 python -m agent.cli "..."   # watch the agent's own browser window
python eval/run_scenarios.py       # planned: runs scenarios.json, prints a results table (stub today)
```

The agent drives its **own** Chromium window, not the browser tabs you have open, so a page you opened
by hand will not change. Each run writes `runs/<time>_<goal>/report.md`; the answer is at the top and
the same top part is printed when the run ends.

The credentials in this repo belong to the mock apps and are not real.

## Models and services

DeepSeek V4 Flash through OpenRouter (OpenAI-compatible, configured in `.env`): reasoning on for the
planner, off for the executor. Playwright (Chromium)
drives the browser; `pdfplumber` reads invoices.

## Assumptions

- Today in the mock world is 2026-10-05 (`APP_TODAY`), so "past due" is judged against that date.
- Amounts are INR with Indian digit grouping; the finance form accepts plain numbers only.
- A single logged-in user per app; no real credentials or third-party systems are touched.

## Known limitations (current)

- There is no results table yet: the 25 scenarios have not been run as a batch, so no autonomy or
  reliability numbers are claimed. The agent was exercised with a scripted stand-in model against the
  real apps (about 15 injected faults) and with the real model in a few manual runs.
- With the real model the plan and the tool calls can drift apart (the plan has one tool call per step,
  and the model sometimes picks a different page than planned).
- The maintenance popup is dismissed by the loop, but the dismiss reloads the page, so a half-filled
  form is lost and has to be filled again.
- The environment is a simulation: two apps, one workflow family (invoice intake, posting,
  reconciliation). Generalization to other workflows is untested.
- Purchase-order / goods-receipt matching and approval routing are not modelled.

## What would be built next

Write the scenario runner, run the 25 scenarios and publish the results table, record the demo, then add
PO matching and a second workflow to test generalization.

## AI assistance

The mock apps were generated with AI coding tools from written specifications (`Website_*/SPEC.md`),
then reviewed and tested. The agent's design and code are to be written and explained by the author.
