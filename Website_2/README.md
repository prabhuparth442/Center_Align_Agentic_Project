# Website 2: Finance System

Mock finance system of record (target system). Invoices from the Mail portal (Website 1) are entered here. Deterministic, fault-injectable, resettable.

## Run
```
python -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/python app.py            # http://127.0.0.1:5002 (env PORT to change); auto-seeds data/
.venv/bin/python seed.py           # manual re-seed
PYTHONPATH= PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest tests/test_finance.py
.venv/bin/playwright install chromium && .venv/bin/python tests/smoke_finance.py   # app must be running
```
Env: `APP_TODAY` (default 2026-10-05), `FAULTS`, `PORT`.

## Credentials
`finance_user` / `finance123`. Cookie: `finance_session`.

## Pages
`/vendors` (search), `/vendors/<id>` (vendor profile + its invoices), `/invoices` (vendor filter), `/invoices/<no>`, `/invoices/new`, `/dashboard`, `/help`.
Seed data: 14 vendors (V001-V006 per SPEC.md, V007-V014 extra, V014 inactive) and 20 invoices (the 2 in SPEC.md plus 18 history rows). Traps are preserved: Orbit Fabricators is not a vendor, and INV-X-1107, INV-XL-0311, INV-BL-2209, INV-OF-5001, INV-NS-0042 do not exist.

## API / utility endpoints
- `GET /api/invoices`, `/api/invoices/<no>`, `/api/vendors`: session or `X-Admin-Token: eval-token`, else 401
- `GET /health`; `POST /reset` (no auth)
- Admin (`X-Admin-Token: eval-token`): `GET /admin/state`, `POST /admin/faults` (`{"faults":"first_submit_fails,rename_label"}`), `GET /admin/log`
- Request log: `data/request_log.jsonl`

## Fault flags (`FAULTS` env or `POST /admin/faults`)
| Flag | Behaviour |
|---|---|
| `slow_ms=N` | delay every response except `/health` and `/admin/*` |
| `error500_once=<path>` | first request to that exact path returns 500 |
| `session_expires_after=N` | after N authenticated HTML GET page loads, next request redirects to `/login?expired=1` |
| `first_submit_fails` | first validation-passing POST to `/invoices/new` is not saved ("Temporary system error. Please try again.") |
| `rename_label` | form labels become "Total Payable (INR)" / "Payment Due By" (ids/names/testids unchanged) |
| `move_submit_button` | submit button rendered above the fields |
| `silent_save_fail` | valid POSTs show success but nothing is persisted |

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
| `slow_submit_ms=N` | a valid invoice submit responds after N ms (still saves) |
| `submit_error500_once` | first valid submit returns 500, nothing saved |
| `save_then_500_once` | first valid submit SAVES the invoice but returns 500 (ambiguous failure) |

Tests: `tests/test_faults_extra.py`.
