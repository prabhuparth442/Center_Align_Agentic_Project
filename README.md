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
HEADLESS=1 SLOW_MO_MS=0 python -m agent.cli "..."     # hide the browser window / full speed (cli default: visible, 300 ms)
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

## If something breaks: where to look

Every run writes `runs/<time>_<goal>/`. Start with `report.md` (answer, plan, what the agent did, verifier)
and then `trace.jsonl` (one line per decision: plan, policy, check, recover, replan, verify, finish).

| Symptom | Likely cause | Look at |
|---|---|---|
| `ERR_NAV_FAILED` right away, 3 retries, `needs_human` | A mock app is not running (`cli` now checks this first) | `Website_*/app.py` running on 5001 / 5002 |
| Status `needs_human`, "Stopped because ..." | The checker gave up after retries, a human decision was needed, or the replan budget ended | `agent/checker.py` (`check`), `agent/loop.py` (`try_replan`) |
| Status `partial`, no answer | The plan ended without a `finish` step | `agent/planner.py` (`PLAN_PROMPT`), `agent/loop.py` end of `run` |
| Policy `block` in the trace | A submit rule fired (duplicate, credit note, inactive vendor, bad amount, unsupported evidence) | `agent/policy.py` (`check_submit`) |
| Invoice saved but status not `success` | The read-back differs from what the agent remembered | `agent/verifier.py` (`verify_invoice`), the Verifier section of the report |
| `ERR_NO_ELEMENT` again and again | The model names an element that is not on the page | `agent/tools/browser.py` (`find_element`, `read_elements`), `data-testid` names in the site templates |
| Agent saw too little of a page | Snapshot is cut at 3000 characters, 25 elements | `agent/tools/browser.py` (`MAX_ELEMENTS`, `TEXT_CHARS`), `agent/executor.py` (`trim`) |
| Value rejected as "not in the document" | `grounded_check` did not find the number in the PDF text | `agent/tools/documents.py` (`grounded_check`) |
| Model call fails or returns bad JSON | Key, model name, or the JSON repair loop | `agent/llm.py` (`structured`), `.env` |
| Terminal looks stuck | Ctrl+C pauses after the current step (`r` resume, `s` stop); a question is waiting for your answer | `agent/cli.py` (`Front`) |

Errors the tools return are stable codes (`ERR_HTTP_5XX`, `ERR_SESSION_EXPIRED`, `ERR_INTERCEPTED`, ...).
`docs/TOOLS.md` lists them, and `agent/checker.py` decides retry / recover / replan / escalate per code.

## Known issues

Each issue is also marked in the code with a `KNOWN ISSUE (README Kn)` comment at the place to change.

| # | Issue | Where |
|---|---|---|
| K1 | One OK tool call counts as one finished plan step. If the model needs two calls for a step, the plan and the real work drift apart. | `agent/loop.py` (step advance) |
| K2 | `finish("success")` is accepted even when plan steps were never run. Only invoice entry is checked (by read-back). | `agent/tools/control.py` (`finish`) |
| K3 | The loop does not catch a failing model call (`LLMError`); `cli.py` does and still writes a report. | `agent/loop.py` (`run`) |
| K4 | Dismissing the maintenance popup reloads the page, so a half-filled form is lost and must be filled again. | `agent/loop.py` (`recover`) |
| K5 | `ERR_PDF_UNREADABLE` is retried on the same file instead of downloading again. | `agent/checker.py` (`RETRYABLE`) |
| K6 | A save that fails but returns a normal page with an error message counts as ok unless the step expects the quoted word "saved". | `agent/checker.py` (`missing_quotes`) |
| K7 | The verifier only compares the JSON record; the second check on the detail page is not done. | `agent/verifier.py` |
| K8 | Reasoning "off" (`effort: none`) is confirmed; the value used for "on" (`high`) is not confirmed for this model. | `agent/llm.py` (`chat`) |
| K9 | `cli.py` plugs into the loop by swapping four names inside `loop.py` for the length of a run. Works, but is the least obvious code. | `agent/cli.py` (`run_in_terminal`) |
| K10 | `eval/run_scenarios.py` is a stub: the 25 scenarios have never been run as a table. Unit tests are thin (5). | `eval/`, `tests/` |
| K11 | The model only sees the last 8 observations. On a long task it forgets early pages unless it saved facts with `remember`. | `agent/executor.py` (`MAX_NOTES`) |

## Known limitations (current)

- There is no results table yet. The agent was exercised with a scripted stand-in model against the real
  apps (about 15 injected faults) and with the real model in a few manual runs, so no reliability numbers
  are claimed.
- Real-model runs: the question-type goal "recent HR mail" ended with an answer read from the inbox; the full invoice
  task has not yet been completed end to end with the real model. Earlier real runs stopped honestly with
  `needs_human` rather than saving a wrong invoice.
- The agent can reach only the two configured apps (origin check in `agent/policy.py`) and writes only one
  thing: the new-invoice form. This is on purpose.
- The environment is a simulation: two apps, one workflow family (invoice intake, posting,
  reconciliation). Generalization to other workflows is untested.
- Purchase-order / goods-receipt matching and approval routing are not modelled.
- The apps must be started by hand before a run.

## Next steps (in this order)

1. Run the full invoice task with the real model a few times and fix what stops it (K1, K2 are the likeliest).
2. Write `eval/run_scenarios.py`, run the 25 scenarios and publish the results table (K10).
3. Give `loop.run` optional `trace` / `oracle` / `before_step` arguments and delete the swapping in `cli.py` (K9).
4. Catch model errors in the loop (K3), step back to the download on a bad PDF (K5), and add the detail-page
   check in the verifier (K7).
5. Record the demo, then add PO matching and a second workflow to test generalization.

## AI assistance

The mock apps were generated with AI coding tools from written specifications (`Website_*/SPEC.md`),
then reviewed and tested. The agent's design and code are to be written and explained by the author.
