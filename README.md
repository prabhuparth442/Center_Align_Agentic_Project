# Autonomous AI Task Worker (CentrAlign AI intern project)

An AI worker that takes a plain-language office task, operates company web apps through a browser,
verifies that the work was really done, and reports evidence. It is being built against a small
simulated company environment (a mail portal and a finance system) so every run is repeatable and
failures can be injected on demand.

> **Status: environment and plan done, agent not built yet.** The table below says what exists. This
> README does not claim results that have not been produced.

| Part | State |
|---|---|
| Mock Mail & Documents portal (`Website_1`, port 5001) | built, 25 tests passing |
| Mock Finance system (`Website_2`, port 5002) | built, 44 tests passing |
| Failure injection (19 flags across both apps) | built and tested, see `docs/FAILURE_INJECTION.md` |
| Scenario matrix (25 scenarios) | written, `scenarios/scenarios.json` |
| Tool plan, permission layer, architecture | designed, see `docs/TOOLS.md`, `docs/ARCHITECTURE.md` |
| Agent skeleton (modules, schemas, tool registry, stubs) | scaffolded, 5 smoke tests; logic **not implemented** |
| Evaluation harness and results | **not implemented**, no results yet |
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
agent/                agent code (config.py present; the rest planned)
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

Agent (once implemented):

```bash
pip install -r requirements.txt && playwright install chromium
cp .env.example .env               # add your LLM endpoint and key
python -m agent.cli "Find the latest invoice from Company X Ltd ..."
python eval/run_scenarios.py       # runs scenarios.json, prints a results table
```

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

- No agent exists yet, so there are no autonomy or reliability results to report.
- The environment is a simulation: two apps, one workflow family (invoice intake, posting,
  reconciliation). Generalization to other workflows is untested.
- Purchase-order / goods-receipt matching and approval routing are not modelled.

## What would be built next

Implement the loop and tools (build order in `docs/ARCHITECTURE.md`), run the 25 scenarios, publish the
results table, record the demo, then add PO matching and a second workflow to test generalization.

## AI assistance

The mock apps were generated with AI coding tools from written specifications (`Website_*/SPEC.md`),
then reviewed and tested. The agent's design and code are to be written and explained by the author.
