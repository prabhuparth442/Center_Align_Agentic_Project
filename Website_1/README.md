# Website 1: Mail & Documents Portal

Mock webmail inbox (source system). Invoices arrive as PDF attachments. Deterministic, fault-injectable, resettable.

## Run
```
python -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/python app.py            # http://127.0.0.1:5001 (env PORT to change); auto-seeds data/
.venv/bin/python seed.py           # manual re-seed
.venv/bin/pytest tests/test_mail.py
.venv/bin/playwright install chromium && .venv/bin/python tests/smoke_mail.py   # app must be running
```
Env: `APP_TODAY` (default 2026-10-05), `FAULTS`, `PORT`.

## Credentials
`demo_user` / `demo123` (Accounts Team, accounts@mycompany.example). Cookie: `mail_session`.

## Routes
`/login`, `/logout` (POST), `/inbox` and `/folder/sent|archive` (`?q=`, `?sort=date_asc`), `/email/<id>` (newer/older links), `/email/<id>/attachment.pdf`, `/email/<id>/attachment`, `/contacts`, `/contact/<address>`, `/help`.

Data: 41 emails (30 inbox, 6 archive, 5 sent). Ids 1-12 are the original invoice scenario emails; 13+ are non-invoice noise with no attachments. PDFs are unchanged (8 files).

## Utility endpoints
- `GET /health`
- `POST /reset` (no auth): re-seed DB + PDFs, restore startup faults, clear counters
- Admin (header `X-Admin-Token: eval-token`): `GET /admin/state`, `POST /admin/faults` (`{"faults":"slow_ms=2500"}`), `GET /admin/log`
- Request log: `data/request_log.jsonl` (`ts, method, path, status, user, fault_applied`)

## Fault flags (`FAULTS` env or `POST /admin/faults`)
| Flag | Behaviour |
|---|---|
| `slow_ms=N` | delay every response except `/health` and `/admin/*` by N ms |
| `error500_once=<path>` | first request to exactly that path returns 500 (`data-testid="error-page"`); cleared by `/reset` |
| `session_expires_after=N` | after N authenticated HTML page loads (inbox, email, preview; not PDF) the next authenticated request redirects to `/login?expired=1`; counter resets on login |

## Extended fault flags (added 2026-10-10)
Always `POST /reset` first, then `POST /admin/faults` (`X-Admin-Token: eval-token`). "Once" faults fire on the
first matching request after a reset; `GET /admin/state` shows them under `counters.fired`. Full catalog and
expected agent behaviour: `../docs/FAILURE_INJECTION.md`.

| Flag | Behaviour |
|---|---|
| `slow_path=/path:MS` | only that path is delayed MS ms |
| `error503_once=/path` | first hit returns 503 with `Retry-After: 1` |
| `fail_first=/path:K` | first K hits return 500 (large K = persistent outage) |
| `drop_connection_once=/path` | connection closed with no response (browser: `ERR_EMPTY_RESPONSE`) |
| `blank_page_once=/path` | HTTP 200 with an empty page |
| `login_flaky_once` | first correct login fails with "Sign-in service temporarily unavailable" |
| `popup_banner` | modal maintenance banner blocks clicks until "Got it"; returns after re-login |
| `pdf_corrupt_once` | first PDF download is truncated (unreadable) |
| `pdf_missing_once` | first PDF download returns 404 |

Tests: `tests/test_faults_extra.py`.
