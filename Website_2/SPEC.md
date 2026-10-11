# Website 2: Finance System (standalone build spec)

This file is self-contained. Build exactly what it lists, nothing more. Work only inside this folder.
Purpose: a small, deterministic, fault-injectable internal finance web app that an AI agent (built later,
separately) will operate through a browser (Playwright). It is the TARGET system of record in a two-app
environment: invoices received by email in a separate Mail portal (Website 1) get entered here.
This app is the ENVIRONMENT, not the product. Keep it simple, stable and fully resettable.
There is no integration between the two apps; the agent moves information between them.

---

## 1. Stack and run

- Python 3.11+, Flask, SQLite (stdlib `sqlite3`), Jinja2 templates.
- Server-rendered HTML with plain `<form>` posts. NO React/Vue or any JS framework. No JavaScript at all is required.
  Minimal CSS in `static/style.css`.
- Binds to `127.0.0.1`, port from env `PORT` (default **5002**). `debug=False`, `threaded=True`.
- Run: `python app.py`. It auto-seeds if `data/` is empty. `python seed.py` re-seeds manually.
- `requirements.txt` (runtime): `flask`. `requirements-dev.txt`: `pytest`, `playwright`, `requests`.

### Folder layout (create exactly this)
```
Website_2/
  app.py              # routes, auth, validation, fault hooks
  seed.py             # deterministic seed (called by app.py and /reset)
  faults.py           # fault-flag parsing and helpers
  money.py            # parse_amount(), format_inr() (see section 5)
  templates/          # base.html, login.html, vendors.html, invoices.html, invoice_detail.html, invoice_new.html, not_found.html, error500.html
  static/style.css
  data/               # finance.db, request_log.jsonl (generated; gitignore data/)
  tests/test_finance.py   # pytest, see section 9
  tests/smoke_finance.py  # Playwright smoke script, see section 9
  README.md           # run commands, credentials, endpoints, fault flags
  requirements.txt  requirements-dev.txt  .gitignore
```

## 2. Determinism rules

- Env `APP_TODAY` (default `2026-10-05`) is the app's notion of "today". ALL validation and stored `created_at`
  use it. Never read the real clock for logic (request-log `ts` may use real time).
- Seed data is hard-coded (section 6). No randomness.
- `POST /reset` restores DB rows exactly, clears runtime faults back to the startup value and clears fault counters.
  Returns JSON `{"status":"reset","app":"finance"}`, completes in under 1 second.

## 3. Auth

- One user: username `finance_user`, password `finance123`.
- Session cookie named `finance_session` (Flask signed cookie, hard-coded secret key).
- Every route except `/login`, `/health`, `/reset`, `/admin/*` redirects to `/login` when not authenticated.
  `/api/*` accepts EITHER a logged-in session OR header `X-Admin-Token: eval-token`; otherwise HTTP 401 JSON `{"error":"unauthorized"}`.
- Wrong credentials: re-render login with flash-error "Invalid username or password" (HTTP 200).
- `GET /login?expired=1` shows flash-error "Your session has expired. Please sign in again."
- `GET /` redirects to `/invoices`.

## 4. Pages and routes

Common: every page except login has a nav bar with links `data-testid="nav-vendors"` (`/vendors`),
`data-testid="nav-invoices"` (`/invoices`), `data-testid="nav-new-invoice"` (`/invoices/new`) and a form with button
`data-testid="logout"` posting to `/logout`. Flash messages render in `data-testid="flash"` with class
`flash-success` or `flash-error`. **Every input, button, link, table and table row has a `data-testid`**
(names below), plus a normal `id`/`name` and a visible `<label for=...>` on inputs. No random or generated ids.

### 4.1 `GET/POST /login`
Inputs `data-testid="username"`, `"password"`; button `data-testid="login-submit"`.

### 4.2 `GET /vendors`
- Table `data-testid="vendors-table"`, rows `data-testid="vendor-row-<vendor_id>"`, columns Vendor ID, Vendor Name, Status (Active/Inactive).
  Inactive vendors ARE listed here.
- Search form (GET): input `data-testid="vendor-search"` (name `q`), button `data-testid="vendor-search-submit"`.
  Case-insensitive substring match on vendor name. Empty q = all. No match: `data-testid="vendors-empty"` "No vendors found."
- Rows sorted by vendor_id ASC.

### 4.3 `GET /invoices`
- Table `data-testid="invoices-table"`, rows `data-testid="invoice-row-<invoice_no>"`.
- Columns: Invoice No (link `data-testid="invoice-link-<invoice_no>"` to `/invoices/<invoice_no>`), Vendor (name),
  Invoice Date, Amount (INR, displayed with Indian grouping, e.g. `1,18,000.00`), Due Date, Status.
- Sorted by invoice_date DESC, ties by invoice_no ASC.
- Filter form (GET): `<select data-testid="filter-vendor" name="vendor_id">` with first option "All vendors" (value empty) then ALL vendors
  (including inactive), and button `data-testid="filter-submit"`. `?vendor_id=V001` filters.
- `data-testid="invoice-count"` shows e.g. `2 invoices`. Empty: `data-testid="invoices-empty"` "No invoices found."

### 4.4 `GET /invoices/<invoice_no>`
- Lookup is case-insensitive. Unknown -> 404 page with `data-testid="not-found"`.
- Fields in elements `data-testid="detail-<field>"` for: `invoice_no`, `vendor` (name), `vendor_id`, `invoice_date`, `amount`
  (Indian grouping with two decimals), `due_date`, `status`, `po_number`, `notes`, `created_by`, `created_at`. Empty optional fields render as empty text.

### 4.5 `GET /invoices/new` and `POST /invoices/new`
One form `<form method="post" data-testid="invoice-form">`:

| Default label | name / id | data-testid | Control | Notes |
|---|---|---|---|---|
| Vendor | `vendor_id` | `field-vendor` | `<select>`: first option `-- select --` (value empty), then ACTIVE vendors only as `<option value="V001">Company X Ltd</option>` | required |
| Invoice Number | `invoice_no` | `field-invoice-no` | text | required |
| Invoice Date | `invoice_date` | `field-invoice-date` | text, placeholder `YYYY-MM-DD` | required |
| Invoice Amount | `amount` | `field-amount` | text | required |
| Due Date | `due_date` | `field-due-date` | text, placeholder `YYYY-MM-DD` | required |
| PO Number | `po_number` | `field-po` | text | optional |
| Notes | `notes` | `field-notes` | textarea | optional |

Submit button `data-testid="submit-invoice"`, text "Save Invoice".

#### Validation (collect ALL errors, show all at once; nothing is saved if any error)
Process after trimming whitespace on every text field. Error messages are exact:

| Field | Rule | Error text (`data-testid="error-<field>"`) |
|---|---|---|
| vendor_id | non-empty | "Please select a vendor." |
| vendor_id | exists and Active (guards tampered POSTs) | "Selected vendor is not available." |
| invoice_no | non-empty | "Invoice number is required." |
| invoice_no | after uppercasing matches `^[A-Z0-9\-]{3,30}$` | "Invoice number must be 3-30 characters: letters, digits and hyphens only." |
| invoice_no | not already present (case-insensitive) | "Invoice number already exists." |
| invoice_date | non-empty, matches `YYYY-MM-DD` and is a real calendar date | "Invoice date must be a valid date in YYYY-MM-DD format." |
| invoice_date | not later than APP_TODAY | "Invoice date cannot be in the future." |
| amount | non-empty | "Amount is required." |
| amount | matches `^\d+(\.\d{1,2})?$` (no commas, spaces, symbols, signs) | "Amount must be a plain number such as 118000.00 (no commas or symbols)." |
| amount | value > 0 | "Amount must be greater than 0." |
| due_date | non-empty, `YYYY-MM-DD`, real date | "Due date must be a valid date in YYYY-MM-DD format." |
| due_date | not before invoice_date (only checked if both dates valid) | "Due date cannot be before the invoice date." |
| due_date | not before APP_TODAY | "Due date cannot be in the past." |
| notes | length <= 500 | "Notes must be 500 characters or fewer." |

On any error: HTTP 200, re-render the form with the user's input preserved (including the selected vendor), a flash-error
"Please fix the errors below.", and the per-field error elements. For a field with several failing rules show only the FIRST failing rule's text.

On success: store the invoice (invoice_no uppercased; amount converted to integer paise; `status` = `Pending Approval`;
`created_by` = logged-in username; `created_at` = APP_TODAY), then redirect (302) to `/invoices/<invoice_no>` with flash-success
`Invoice <invoice_no> saved.` (flash must survive the redirect).

No edit or delete UI or routes exist. A wrong entry is permanent until `/reset`; this is intentional.

### 4.6 `POST /logout`, 404 and 500 pages as above.

## 5. Money helpers (`money.py`)

- `parse_amount("118000.00") -> 11800000` (integer paise). Use `decimal.Decimal`, never floats.
- `format_inr(11800000) -> "1,18,000.00"`; `format_inr(9840000) -> "98,400.00"`; `format_inr(708000) -> "7,080.00"`;
  `format_inr(10000000) -> "1,00,000.00"`. Indian grouping: last three integer digits, then groups of two.
- Include unit tests for these in `tests/test_finance.py`.

## 6. Seed data (exact)

### 6.1 vendors(vendor_id PK, name, status)
| vendor_id | name | status |
|---|---|---|
| V001 | Company X Ltd | Active |
| V002 | Company X Logistics Pvt Ltd | Active |
| V003 | Acme Supplies | Active |
| V004 | Brightline Services | Active |
| V005 | Nova Stationers | Active |
| V006 | Delta Packaging | Inactive |

"Orbit Fabricators" is intentionally NOT a vendor.

### 6.2 invoices(invoice_no PK, vendor_id, invoice_date, amount_paise INTEGER, due_date, status, po_number, notes, created_by, created_at)
| invoice_no | vendor_id | invoice_date | amount_paise | due_date | status | po_number | notes | created_by | created_at |
|---|---|---|---|---|---|---|---|---|---|
| INV-X-1042 | V001 | 2026-08-28 | 9840000 | 2026-09-27 | Paid | (empty) | (empty) | seed | 2026-08-29 |
| INV-AC-778 | V003 | 2026-09-15 | 2360000 | 2026-10-15 | Pending Approval | (empty) | (empty) | seed | 2026-09-16 |

These are inserted directly by the seed, bypassing form validation (INV-X-1042's due date is already in the past).

## 7. Admin and utility endpoints (no UI)

- `GET /health` -> `{"status":"ok","app":"finance","today":"2026-10-05"}`
- `POST /reset` -> see section 2. No auth.
- `GET /api/invoices` -> JSON list, each item
  `{"invoice_no","vendor_id","vendor_name","invoice_date","amount": 118000.0,"amount_paise": 11800000,"due_date","status","po_number","notes","created_by","created_at"}`.
  Sorted like the invoices page. Auth per section 3.
- `GET /api/invoices/<invoice_no>` -> one item (case-insensitive lookup) or 404 `{"error":"not_found"}`.
- `GET /api/vendors` -> `[{"vendor_id","name","status"}]`.
- `GET /admin/state` (header `X-Admin-Token: eval-token`, else 403) -> `{"app":"finance","today","vendors":[...],"invoices":[...same shape as API...],"faults":"<current flag string>","counters":{"auth_page_loads":n,"valid_submits_seen":n,"error500_fired":[paths],"first_submit_failed":bool}}`.
- `POST /admin/faults` (same token) body `{"faults":"first_submit_fails,rename_label"}` replaces the active flags at runtime.
- `GET /admin/log` (same token) returns the request log as a JSON list.
- Request log: every HTTP request appends one JSON line to `data/request_log.jsonl`:
  `{"ts": <ISO real time>, "method", "path", "status", "user", "fault_applied": <flag name or null>}`.

## 8. Fault flags

Env var `FAULTS` (comma-separated `name` or `name=value`) at startup, plus runtime change via `POST /admin/faults`.
Unknown names are ignored (log once to stderr). When a flag takes effect, record it in `fault_applied`.

| Flag | Behaviour |
|---|---|
| `slow_ms=N` | Every response except `/health` and `/admin/*` is delayed by N ms. |
| `error500_once=<path>` | The FIRST request whose path (query string excluded) equals `<path>` returns HTTP 500 (`templates/error500.html`, `data-testid="error-page"`, text "Internal Server Error"). Later requests are normal. Recorded in `counters.error500_fired`; cleared by `/reset`. |
| `session_expires_after=N` | Per-session counter `auth_page_loads` counts authenticated HTML GET page loads (vendors, invoices, invoice detail, new-invoice form); NOT `/api/*`, NOT POSTs, NOT admin or health. Resets to 0 on successful login. When it already equals N, the NEXT authenticated request clears the session and redirects (302) to `/login?expired=1`. |
| `first_submit_fails` | The FIRST `POST /invoices/new` that passes ALL validation is NOT saved: re-render the form (input preserved) with flash-error "Temporary system error. Please try again." and no field errors. Validation-failing POSTs do not consume the flag. The second valid POST saves normally. Sets `counters.first_submit_failed=true`; cleared by `/reset`. |
| `rename_label` | On the new-invoice form only, the label texts change: "Invoice Amount" -> "Total Payable (INR)" and "Due Date" -> "Payment Due By". `id`, `name` and `data-testid` do NOT change. |
| `move_submit_button` | On the new-invoice form, render the submit button ABOVE the fields instead of below (same testid). |
| `silent_save_fail` | EVERY valid `POST /invoices/new` shows the normal success redirect and flash-success but does NOT persist the record (it will not appear in the list, detail page, `/api/invoices` or admin state; the detail page the redirect points to returns 404). Increment `counters.valid_submits_seen` for each. |

## 9. Tests and smoke script (deliverables)

`tests/test_finance.py` (pytest, Flask test client):
1. `format_inr` / `parse_amount` examples from section 5, plus rejection of `1,18,000.00`, `Rs 100`, `-5`, `0`, `100.123`.
2. After reset: 6 vendors, 2 invoices; `/invoices` lists INV-AC-778 before INV-X-1042 (invoice_date DESC).
3. The new-invoice dropdown contains V001-V005 and NOT V006.
4. Every validation row in section 4.5 produces its exact error text (use a parametrised test); multiple errors at once are all shown;
   input is preserved on error; nothing is saved on error.
5. Valid submit of (V001, `inv-x-1107`, 2026-09-30, `118000.00`, 2026-10-30) redirects to `/invoices/INV-X-1107`, flash `Invoice INV-X-1107 saved.`,
   `/api/invoices/INV-X-1107` returns amount 118000.0, status `Pending Approval`, created_by `finance_user`, created_at `2026-10-05`.
   A second submit of the same number shows "Invoice number already exists."
6. Past-due invoice (due 2026-09-25) is rejected with "Due date cannot be in the past."
7. Each fault flag behaves as in section 8; `/reset` clears them. In particular: `silent_save_fail` shows success but the invoice is absent from `/api/invoices`;
   `first_submit_fails` consumes only on a validation-passing POST.
8. `/api/*` returns 401 without session or token; `/admin/state` returns 403 without the token.

`tests/smoke_finance.py` (Playwright sync API, app running on 5002): log in as finance_user, open the new-invoice form, add
`INV-TEST-0001` (vendor Acme Supplies, invoice date 2026-10-01, amount `1000.00`, due date 2026-11-01), assert the success flash and the detail page values,
confirm via `GET /api/invoices/INV-TEST-0001`, then call `POST /reset` and confirm it is gone. Exit 0 on success.

## 10. Acceptance checklist
- [ ] `python app.py` starts on 5002; `/health` OK; login works with finance_user/finance123.
- [ ] Vendors and invoices match section 6 exactly; amounts display with Indian grouping.
- [ ] All `data-testid` names in section 4 exist; no JS framework.
- [ ] Every validation message matches section 4.5 exactly; all errors are shown together.
- [ ] `/reset` is idempotent and fast; faults and counters cleared by reset.
- [ ] Each fault flag behaves exactly as in section 8.
- [ ] pytest passes; smoke script exits 0.
- [ ] README lists run commands, credentials, endpoints and fault flags.
