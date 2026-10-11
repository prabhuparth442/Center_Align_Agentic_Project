# Agent tool plan

Design rules
1. The model chooses tools; **code** enforces what is allowed (permission layer) and **code** checks
   results (checker, verifier). The model never writes a value into a system directly.
2. Every tool returns a small structured result: `{ok, data, error_code, error_message, evidence}`.
   Errors have stable codes so the loop can branch on them without asking the model.
3. Observations are compact (target under about 3,000 characters per call). The browser snapshot lists
   interactive elements by `data-testid` / label, not raw HTML.
4. Credentials live in config, never in a prompt. The model asks for "log in to finance"; the tool
   supplies the (sandbox) credentials.
5. Every call is written to the audit trace (see ARCHITECTURE.md).

## Tools

### A. Browser (Playwright, DOM-based, one browser context, one tab per app)
| Tool | Args | Returns | Notes |
|---|---|---|---|
| `browser_open` | `app: "mail"\|"finance"`, `path: str = "/"` | status code, final URL, title | only the two configured origins are allowed; records HTTP status and navigation errors; after a navigation error wait ~0.5 s before the retry (Chromium otherwise reports the retry as interrupted) |
| `browser_snapshot` | `app` | URL, title, visible text (trimmed), list of interactive elements `{testid, role, label, text, enabled}`, banner/overlay flag, flash messages | the main observation; also reports `expected_missing` if a page has none of the usual landmarks (blank page) |
| `browser_click` | `app`, `target` (testid or visible text) | clicked element, resulting URL, new status | if blocked by an overlay returns `ERR_INTERCEPTED` with the blocker's testid |
| `browser_fill` | `app`, `target`, `value` | the value now in the field | |
| `browser_select` | `app`, `target`, `option` (label or value) | selected option | |
| `browser_download` | `app`, `target` | saved path under `runs/<id>/downloads/`, size, sha256 | download errors map to `ERR_DOWNLOAD` / `ERR_HTTP_404` |
| `browser_login` | `app` | logged-in flag | uses configured sandbox credentials; handles `/login?expired=1` and the transient login error |

### B. Documents
| Tool | Args | Returns | Notes |
|---|---|---|---|
| `read_pdf` | `path` | text lines per page | `ERR_PDF_UNREADABLE` when truncated/corrupt (triggers re-download) |
| `grounded_check` | `field`, `value`, `source_ref` | `{grounded: bool, quote}` | passes only if the value appears in the source text after normalising Indian digit grouping and whitespace; used on **every** extracted value before it is written |

### C. Read-back (independent of the UI path that did the write)
| Tool | Args | Returns | Notes |
|---|---|---|---|
| `finance_get_invoice` | `invoice_no` | the record or `ERR_NOT_FOUND` | calls the finance JSON API with the logged-in session; read-only |
| `finance_list_invoices` | `vendor_id?` | records | used by the reconciliation task |
| `finance_list_vendors` | `query?` | vendor id, name, status | used to resolve the vendor before writing |

### D. Control
| Tool | Args | Returns | Notes |
|---|---|---|---|
| `remember` | `key`, `value`, `evidence` | ack | run memory: structured facts with provenance (where the value was seen) |
| `recall` | `key?` | stored facts | |
| `ask_human` | `question`, `options?` | the answer | pauses the run; in eval a scripted oracle answers (`oracle_answers` in the scenario) |
| `request_approval` | `action_summary`, `risk` | `approved: bool` | required before the write (see policy) |
| `finish` | `status: success\|partial\|failed\|needs_human`, `summary`, `evidence[]` | ends the run | the loop refuses `success` unless the verifier passed |

There is deliberately **no** edit, delete, or arbitrary-URL tool. The agent cannot do more than the
workflow needs.

## Permission layer (enforced in `policy.py`, not by the prompt)

| Rule | Behaviour |
|---|---|
| Origins | navigation only to the configured mail and finance base URLs |
| Writes | only one write action exists: submitting the finance new-invoice form |
| Approval gate | `request_approval` required when: vendor was ambiguous and resolved by a guess, a value was **inferred** (e.g. due date from "Net 30"), amount is above `APPROVAL_THRESHOLD_INR`, or the invoice is past due |
| Block | refuse the write if any field failed `grounded_check`, the vendor is missing or inactive, or the document is a credit note |
| Idempotency | before retrying a submit that did not end in a clean success (error status, timeout, dropped connection), the agent must call `finance_get_invoice`; if the record exists the submit is **not** repeated |
| Submit budget | at most one successful submit per invoice number per run |

## Error codes the loop branches on

`ERR_HTTP_5XX` / `ERR_HTTP_503` (retry, honour `Retry-After`) · `ERR_NAV_FAILED` (dropped connection, retry) ·
`ERR_BLANK_PAGE` (reload) · `ERR_TIMEOUT` (wait/poll, then verify) · `ERR_INTERCEPTED` (dismiss overlay) ·
`ERR_SESSION_EXPIRED` (re-login, resume) · `ERR_LOGIN_TRANSIENT` (retry login) · `ERR_PDF_UNREADABLE` (re-download) ·
`ERR_HTTP_404` · `ERR_VALIDATION` (field errors from the form, shown to the model) · `ERR_NOT_FOUND` · `ERR_POLICY` (blocked by the permission layer) ·
`ERR_NO_ELEMENT` (target not on the page, take a snapshot) · `ERR_DOWNLOAD` (link is not a PDF) · `ERR_LOGIN_FAILED` (wrong credentials, not retried) ·
`ERR_NEEDS_HUMAN` (ask_human / request_approval with nobody to answer, the run stops as needs_human).

## How the main task flows through the tools

1. `browser_login(mail)`, `browser_open(mail,/inbox)`, search "Company X Ltd" via `browser_fill` + `browser_click`.
2. Snapshot the results; the model picks the newest message that is an invoice (not the credit note, not the resend).
   Ambiguous vendor name: `ask_human`.
3. `browser_download` the PDF, `read_pdf`, model proposes `{invoice_no, invoice_date, due_date, amount, vendor}`;
   `grounded_check` each value; `remember` them with their source.
4. `finance_list_vendors` to resolve the vendor id; `finance_get_invoice` to see if it already exists.
5. Policy decides whether `request_approval` is needed.
6. `browser_login(finance)`, `browser_open(finance,/invoices/new)`, `browser_select`/`browser_fill` each field, `browser_click` submit.
7. Read-back with `finance_get_invoice` and compare every field with what was extracted; also open the detail page as a second check.
8. `finish(success, summary, evidence)` with quotes from the PDF, the saved record, and the trace file path.

## Model and action format (DeepSeek V4 Flash)

One model via an OpenAI-compatible endpoint (OpenRouter). Planner calls use reasoning on; executor and
verifier calls use `reasoning: {"effort": "none"}` (the pattern used in the IBY project). The executor
replies with JSON `{"tool": ..., "args": {...}}`, validated by pydantic; invalid output is sent back for
repair (max 2 tries). Unverified: accepted `reasoning` values for "on", and native tool-calling.
