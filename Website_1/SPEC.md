# Website 1: Mail & Documents Portal (standalone build spec)

This file is self-contained. Build exactly what it lists, nothing more. Work only inside this folder.
Purpose: a small, deterministic, fault-injectable webmail inbox that an AI agent (built later, separately)
will operate through a browser (Playwright). It is the SOURCE system in a two-app environment: invoices
arrive here as PDF attachments, and a second app (Website 2, Finance system) is where they get entered.
This app is the ENVIRONMENT, not the product. Keep it simple, stable and fully resettable.

---

## 1. Stack and run

- Python 3.11+, Flask, SQLite (stdlib `sqlite3`), Jinja2 templates. PDF generation with `fpdf2`.
- Server-rendered HTML with plain forms. NO React/Vue or any JS framework. Minimal CSS in `static/style.css`.
  No JavaScript at all is required.
- Binds to `127.0.0.1`, port from env `PORT` (default **5001**). `debug=False`, `threaded=True`.
- Run: `python app.py`. It auto-seeds (DB + PDFs) if `data/` is empty. `python seed.py` re-seeds manually.
- `requirements.txt` (runtime): `flask`, `fpdf2`. `requirements-dev.txt`: `pytest`, `playwright`, `pdfplumber`, `requests`.

### Folder layout (create exactly this)
```
Website_1/
  app.py              # routes, auth, fault hooks
  seed.py             # deterministic seed: SQLite rows + PDF generation (called by app.py and /reset)
  faults.py           # fault-flag parsing and helpers
  templates/          # base.html, login.html, inbox.html, email.html, attachment.html, error500.html
  static/style.css
  data/               # mail.db, attachments/*.pdf, request_log.jsonl (all generated; gitignore data/)
  tests/test_mail.py  # pytest, see section 8
  tests/smoke_mail.py # Playwright smoke script, see section 8
  README.md           # run commands, credentials, endpoints, fault flags
  requirements.txt  requirements-dev.txt  .gitignore
```

## 2. Determinism rules

- Env `APP_TODAY` (default `2026-10-05`) is the app's notion of "today". Never read the real clock for any
  displayed or logic value (request log may use real time for `ts`).
- Seed data is hard-coded (section 5). No randomness anywhere.
- PDF generation must be deterministic in content. Set a fixed PDF creation date (e.g. `2026-10-05`) if the
  library allows; byte-identical output is nice-to-have, identical extractable text is required.
- `POST /reset` restores DB rows, regenerates all PDFs, clears runtime faults back to the startup value and
  clears fault counters. Returns JSON `{"status":"reset","app":"mail"}`, completes in under 1 second.

## 3. Auth

- One user: username `demo_user`, password `demo123`. Display name "Accounts Team", address `accounts@mycompany.example`.
- Session cookie named `mail_session` (Flask signed cookie is fine; secret key hard-coded in code).
- Every route except `/login`, `/health`, `/reset`, `/admin/*` redirects to `/login` when not authenticated.
- Wrong credentials: re-render login with flash-error "Invalid username or password" (HTTP 200).
- `GET /login?expired=1` shows flash-error "Your session has expired. Please sign in again."
- `GET /` redirects to `/inbox`.

## 4. Pages and routes

Common: every page except login has a top bar with link `data-testid="nav-inbox"` (to `/inbox`) and a form with
button `data-testid="logout"` posting to `/logout` (redirects to `/login`).
Flash messages render in an element with `data-testid="flash"` and class `flash-success` or `flash-error`.
**Every input, button, link, table and table row has a `data-testid`** (names below), plus a normal `id`/`name`
and a visible `<label for=...>` on inputs. No random or generated ids.

### 4.1 `GET/POST /login`
- Inputs: `data-testid="username"`, `data-testid="password"`; button `data-testid="login-submit"`.

### 4.2 `GET /inbox`
- Table `data-testid="inbox-table"`; one row per email `data-testid="email-row-<id>"`.
- Columns: From (sender name and `<address>`), Subject (a link `data-testid="email-link-<id>"` to `/email/<id>`),
  Date (YYYY-MM-DD), Attachment (text `PDF` if an attachment exists, else empty).
- Default sort date DESC, ties broken by id DESC. `?sort=date_asc` sorts date ASC, ties by id ASC.
  Sort links: `data-testid="sort-newest"`, `data-testid="sort-oldest"`.
- Search form (GET): input `data-testid="search-input"` (name `q`), button `data-testid="search-submit"`.
  `?q=` is a case-insensitive substring match over sender name, sender address and subject. Empty q = all.
- Element `data-testid="inbox-count"` shows e.g. `12 messages` (count of rows currently displayed).
- No pagination. Zero results shows `data-testid="inbox-empty"` with text "No messages found."

### 4.3 `GET /email/<id>`
- 404 page (plain, `data-testid="not-found"`) for unknown id.
- Elements: `data-testid="email-from"` (name and address), `"email-to"`, `"email-date"`, `"email-subject"`,
  `"email-body"` (plain text, line breaks preserved with `white-space: pre-wrap`).
- If the email has an attachment: link `data-testid="attachment-link"` to `/email/<id>/attachment.pdf` and link
  `data-testid="attachment-preview"` to `/email/<id>/attachment`. Show the filename in `data-testid="attachment-name"`.

### 4.4 `GET /email/<id>/attachment.pdf`
- Returns the PDF, `Content-Type: application/pdf`, `Content-Disposition: attachment; filename="<invoice_or_cn_no>.pdf"`.
- 404 if the email has no attachment.

### 4.5 `GET /email/<id>/attachment`
- HTML view of the SAME document: render each PDF text line as a `<div>` inside `data-testid="attachment-text"`
  (monospace, same order and wording as the PDF). Numbers must match the PDF exactly.

### 4.6 `POST /logout`

No compose, reply, delete, labels, folders or read/unread state. The app is read-only apart from `/reset`.

## 5. Seed data (exact)

Recipient for all emails: `Accounts Team <accounts@mycompany.example>`. Table `emails`:
`id INTEGER PK, sender_name, sender_email, subject, date TEXT (YYYY-MM-DD), body TEXT, attachment_filename TEXT NULL`.

| id | sender (name <address>) | subject | date | attachment |
|---|---|---|---|---|
| 1 | Company X Ltd <billing@companyx.example> | Invoice INV-X-1042 for August services | 2026-08-28 | INV-X-1042.pdf |
| 2 | Company X Ltd <billing@companyx.example> | Invoice INV-X-1107, September services | 2026-09-30 | INV-X-1107.pdf |
| 3 | Company X Ltd <billing@companyx.example> | Reminder: Invoice INV-X-1107 | 2026-10-02 | INV-X-1107.pdf |
| 4 | Company X Logistics Pvt Ltd <accounts@companyxlogistics.example> | Invoice INV-XL-0311 | 2026-10-01 | INV-XL-0311.pdf |
| 5 | Acme Supplies <ar@acme-supplies.example> | Invoice INV-AC-778 | 2026-09-15 | INV-AC-778.pdf |
| 6 | Brightline Services <billing@brightline.example> | Invoice INV-BL-2209 | 2026-09-20 | INV-BL-2209.pdf |
| 7 | Offers Desk <promo@bulkdeals.example> | Quarterly offers just for you | 2026-10-01 | none |
| 8 | HR Team <hr@mycompany.example> | Office closed on Oct 20 | 2026-10-03 | none |
| 9 | Orbit Fabricators <sales@orbitfab.example> | Invoice INV-OF-5001 | 2026-09-25 | INV-OF-5001.pdf |
| 10 | Nova Stationers <ar@novastationers.example> | Invoice INV-NS-0042 | 2026-09-10 | INV-NS-0042.pdf |
| 11 | Company X Ltd <billing@companyx.example> | Credit note CN-X-0009 | 2026-10-03 | CN-X-0009.pdf |
| 12 | Acme Supplies <ar@acme-supplies.example> | Re: INV-AC-778 payment query | 2026-10-04 | none |

Email 3 uses the identical PDF as email 2 (same text, filename `INV-X-1107.pdf`).

### 5.1 Email bodies (use EXACTLY these; do not add amounts, dates or other numbers to any body)
Bodies are deliberate: only email 2's body mentions an amount, as a trap.
- 1: `Hello,\n\nPlease find attached invoice INV-X-1042 for August services.\n\nRegards,\nCompany X Billing`
- 2: `Hello,\n\nPlease find attached our invoice for the September work, approx. Rs 1,00,000 plus GST.\n\nRegards,\nCompany X Billing`
- 3: `Hello,\n\nThis is a reminder about the attached invoice. Please confirm receipt.\n\nRegards,\nCompany X Billing`
- 4: `Hi,\n\nAttached is our latest invoice.\n\nRegards,\nAccounts, Company X Logistics`
- 5: `Dear team,\n\nPlease find attached invoice INV-AC-778.\n\nThanks,\nAcme Supplies AR`
- 6: `Hello,\n\nThe invoice for the services delivered is attached. Terms as per contract.\n\nBrightline Billing`
- 7: `Save big on bulk orders this quarter. Visit our website to see the latest offers.\n\nOffers Desk`
- 8: `Dear all,\n\nThe office will be closed on 20 October for the festival holiday. Please plan accordingly.\n\nHR Team`
- 9: `Hello,\n\nPlease find attached the invoice for the fabrication work.\n\nOrbit Fabricators Sales`
- 10: `Dear customer,\n\nAttached is the invoice for stationery supplies.\n\nNova Stationers AR`
- 11: `Hello,\n\nPlease find attached a credit note against a returned item. This is not a payment request.\n\nRegards,\nCompany X Billing`
- 12: `Hello,\n\nCould you let us know when payment for INV-AC-778 will be made?\n\nThanks,\nAcme Supplies AR`

### 5.2 PDF documents
A4, text only, Helvetica (core font). **Do NOT use the rupee sign glyph** (core fonts lack it): write `INR`.
Indian digit grouping everywhere (`1,18,000.00`, `98,400.00`, `7,080.00`). PDFs must have a real extractable text layer
(verify with pdfplumber). One text item per line, in this order (omit lines marked omit):

```
INVOICE
<Vendor legal name>
Invoice No: <no>
Invoice Date: <YYYY-MM-DD>
Due Date: <YYYY-MM-DD>              (omit for INV-BL-2209)
Payment terms: Net 30               (ONLY for INV-BL-2209)
Bill To: My Company Pvt Ltd
Description                          Qty    Amount (INR)
<line item text>                     1      <amount>
Subtotal: INR <amount>              (ONLY for INV-X-1107)
GST @18%: INR <amount>              (ONLY for INV-X-1107)
Total Payable: INR <amount>
```

| Document | Vendor legal name | Invoice No | Invoice Date | Due Date | Line item | Total Payable |
|---|---|---|---|---|---|---|
| INV-X-1042 | Company X Ltd | INV-X-1042 | 2026-08-28 | 2026-09-27 | Consulting services Aug 2026 | 98,400.00 |
| INV-X-1107 | Company X Ltd | INV-X-1107 | 2026-09-30 | 2026-10-30 | Consulting services Sep 2026 (line amount 1,00,000.00; Subtotal 1,00,000.00; GST 18,000.00) | **1,18,000.00** |
| INV-XL-0311 | Company X Logistics Pvt Ltd | INV-XL-0311 | 2026-10-01 | 2026-10-31 | Freight handling Sep 2026 | 56,250.00 |
| INV-AC-778 | Acme Supplies | INV-AC-778 | 2026-09-15 | 2026-10-15 | Office supplies batch | 23,600.00 |
| INV-BL-2209 | Brightline Services | INV-BL-2209 | 2026-09-20 | (none, Net 30 line instead) | Maintenance services Sep 2026 | 45,000.00 |
| INV-OF-5001 | Orbit Fabricators | INV-OF-5001 | 2026-09-25 | 2026-10-25 | Fabrication work order 5001 | 78,900.00 |
| INV-NS-0042 | Nova Stationers | INV-NS-0042 | 2026-09-10 | 2026-09-25 | Stationery supplies | 7,080.00 |

Credit note CN-X-0009 uses this layout instead:
```
CREDIT NOTE
Company X Ltd
Credit Note No: CN-X-0009
Date: 2026-10-03
Bill To: My Company Pvt Ltd
Description                          Qty    Amount (INR)
Returned item credit                 1      -10,000.00
Total Credit: INR -10,000.00
```
There must be NO other numbers in the PDFs that could be mistaken for the payable amount (no quantities beyond `1`,
no rates, no phone numbers, no bank details).

### 5.3 Why the traps exist (do not "fix" them)
- 2 vs 3: duplicate resend. 2's body mentions the pre-GST figure; the PDF's Total Payable is authoritative.
- 4: different vendor whose name contains "Company X"; newest of any "Company X*" sender.
- 6: no due date, only "Net 30". 9: vendor absent from the finance system. 10: due date already past.
- 11: newest email from Company X Ltd but not an invoice. 7, 8, 12: noise.

## 6. Admin and utility endpoints (no UI)

- `GET /health` -> `{"status":"ok","app":"mail","today":"2026-10-05"}`
- `POST /reset` -> see section 2. No auth.
- `GET /admin/state` (header `X-Admin-Token: eval-token`, else 403) -> JSON
  `{"app":"mail","today":...,"emails":[{id,sender_name,sender_email,subject,date,attachment_filename}],"faults":"<current flag string>","counters":{"auth_page_loads":n,"error500_fired":[paths]}}`
- `POST /admin/faults` (same token) body `{"faults":"slow_ms=2500,error500_once=/inbox"}` replaces the active flags at runtime.
- `GET /admin/log` (same token) returns the request log as a JSON list.
- Request log: every HTTP request appends one JSON line to `data/request_log.jsonl`:
  `{"ts": <ISO real time>, "method", "path", "status", "user", "fault_applied": <flag name or null>}`.

## 7. Fault flags

Env var `FAULTS` (comma-separated `name` or `name=value`) at startup, plus runtime change via `POST /admin/faults`.
Unknown flag names are ignored (logged once to stderr). Every time a flag takes effect, set `fault_applied` in the log line.

| Flag | Behaviour |
|---|---|
| `slow_ms=N` | Every response except `/health` and `/admin/*` is delayed by N ms (`time.sleep`). |
| `error500_once=<path>` | The FIRST request whose path (query string excluded) exactly equals `<path>` (e.g. `/inbox`, `/email/2`) returns HTTP 500 with a plain page (`templates/error500.html`, `data-testid="error-page"`, text "Internal Server Error"). Later requests behave normally. The fired path is recorded in `counters.error500_fired`; cleared by `/reset`. |
| `session_expires_after=N` | A per-session counter `auth_page_loads` counts authenticated HTML page loads (inbox, email view, attachment preview; NOT the PDF download, NOT admin or health). It resets to 0 on successful login. When it already equals N, the NEXT authenticated request clears the session and redirects (302) to `/login?expired=1`. |

## 8. Tests and smoke script (deliverables)

`tests/test_mail.py` (pytest, uses Flask test client):
1. After reset: 12 emails. Default inbox order (date DESC, ties by id DESC) must be exactly ids
   `12, 11, 8, 3, 7, 4, 2, 9, 6, 5, 10, 1`; `?sort=date_asc` (date ASC, ties by id ASC) must give
   `1, 10, 5, 6, 9, 2, 4, 7, 3, 8, 11, 12`.
2. Search `company x` returns ids 1, 2, 3, 4, 11 only (case-insensitive substring on sender or subject).
3. Auth redirect for `/inbox` when logged out; login success and failure messages.
4. PDF download for emails 2 and 3 returns `application/pdf`; extracted text (pdfplumber) contains `Total Payable: INR 1,18,000.00`
   and `Due Date: 2026-10-30`; email 6's PDF contains `Payment terms: Net 30` and NO `Due Date`; email 11's contains `CREDIT NOTE`.
5. The attachment HTML preview text equals the PDF text, line for line, after collapsing runs of whitespace to single spaces on both sides.
6. Each fault flag behaves as in section 7, including reset clearing them.
7. `/admin/state` without the token returns 403.

`tests/smoke_mail.py` (Playwright sync API, run with the app already running on 5001): log in as demo_user, search
`Company X Ltd`, open the newest email whose subject starts with `Invoice` from Company X Ltd, download the PDF through
`attachment-link`, extract text with pdfplumber, assert amount `1,18,000.00` and due date `2026-10-30`, print the
extracted values, exit 0. This proves the source side is solvable by browser automation.

## 9. Acceptance checklist
- [ ] `python app.py` starts on 5001; `/health` OK; login works with demo_user/demo123.
- [ ] 12 emails with the exact subjects, dates, senders and bodies from section 5; 9 emails have attachments, backed by 8 distinct PDF files (email 3 reuses email 2's file).
- [ ] All `data-testid` names in section 4 are present.
- [ ] PDF text is extractable and matches section 5.2 exactly; no rupee glyph; no stray numbers.
- [ ] `/reset` is idempotent and fast; faults and counters cleared by reset.
- [ ] pytest passes; smoke script exits 0.
- [ ] README lists run commands, credentials, endpoints and fault flags.
