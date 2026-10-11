# Failure injection

Both mock apps can misbehave on demand so the agent's reliability can be measured, not claimed.
Faults are switched with the `FAULTS` env var at startup or at runtime with
`POST /admin/faults` (header `X-Admin-Token: eval-token`, body `{"faults": "name=value,name2"}`).

**Per-scenario recipe (the eval harness does exactly this):**
1. `POST /reset` on both apps (clears data, runtime faults and every "fired once" counter).
2. `POST /admin/faults` on the app(s) the scenario needs.
3. Run the agent with the goal. 4. Score from `GET /admin/state`, `GET /admin/log` and the agent trace.

A fault marked "once" fires on the first matching request after a reset, then behaves normally.
Fired counters are visible in `GET /admin/state` under `counters.fired`; each request's log line
carries `fault_applied`.

## Catalog

| Flag | App | What happens | What the agent sees |
|---|---|---|---|
| `slow_ms=N` | both | every response delayed N ms | slow loads, no error |
| `slow_path=/path:MS` | both | only that path delayed (e.g. `slow_path=/invoices/new:6000`) | one slow page, possible timeout |
| `error500_once=/path` | both | first hit returns HTTP 500 | error page (`data-testid="error-page"`) |
| `error503_once=/path` | both | first hit returns HTTP 503 with `Retry-After: 1` | "Service Unavailable" page |
| `fail_first=/path:K` | both | first K hits return HTTP 500 (K large = persistent outage) | repeated errors |
| `drop_connection_once=/path` | both | connection closed with no response | navigation error (empty response / reset) |
| `blank_page_once=/path` | both | HTTP 200 but an empty page | page loads with no expected elements |
| `session_expires_after=N` | both | after N authenticated page loads, next request redirects to `/login?expired=1` | surprise logout mid-task |
| `login_flaky_once` | both | first correct login fails with "Sign-in service temporarily unavailable" | login error that is not a wrong password |
| `popup_banner` | both | modal "maintenance" overlay blocks the page until "Got it" is clicked (returns after re-login) | clicks blocked by an overlay |
| `pdf_corrupt_once` | mail | first PDF download is truncated | file that cannot be opened |
| `pdf_missing_once` | mail | first PDF download returns 404 | missing attachment |
| `first_submit_fails` | finance | first valid submit is not saved; "Temporary system error" flash | failed save, clear message |
| `submit_error500_once` | finance | first valid submit returns 500, nothing saved | failed save, no data written |
| `save_then_500_once` | finance | first valid submit **saves the invoice** but returns 500 | **ambiguous failure**: error shown, record exists |
| `slow_submit_ms=N` | finance | valid submit takes N ms to respond (still saves) | timeout while the save may still land |
| `silent_save_fail` | finance | success shown but nothing saved (every time) | false success, only read-back reveals it |
| `rename_label` | finance | form labels reworded (ids/testids unchanged) | label text no longer matches |
| `move_submit_button` | finance | submit button moves above the fields | layout change |

Unknown flag names are ignored (warned once on stderr).

## What a good agent does

| Situation | Detect by | Correct behaviour | Must NOT |
|---|---|---|---|
| Transient 5xx / 503 / blank page / dropped connection | status code, missing expected element, navigation error | retry with backoff (honour `Retry-After`), reload; give up after the retry budget and report | loop forever, or invent page content |
| Overlay banner | click blocked / banner element in snapshot | dismiss it, continue | treat it as task content |
| Login fails but credentials are right | message is "temporarily unavailable", not "Invalid username or password" | retry login once or twice | give up, or change credentials |
| Session expired | redirect to `/login?expired=1` | log in again with the configured credentials, resume where it was | restart the whole task or double-submit |
| Corrupt or missing PDF | PDF parser error, 404 | re-download; if still bad, ask a human | extract values from nothing |
| Submit returns an error (`submit_error500_once`, `save_then_500_once`) or times out (`slow_submit_ms`) | status, timeout | **read back first** (does the invoice exist now?), only then decide to retry | resubmit blindly (duplicate / "already exists" error) |
| `silent_save_fail` | read-back does not show the invoice | report failure honestly; retry once; escalate | report success because a success banner appeared |
| Persistent outage (`fail_first=/path:99`) | repeated failures past budget | stop, write nothing, report `needs_human` with evidence | claim success |
| Label or layout drift | locator by label fails | fall back to stable ids/test ids or re-read the snapshot | give up on first miss |

The write path is protected by an idempotency rule in the permission layer: before any retry of a
submit that did not return a clean success, the agent must read the system state back.

## Scenario matrix

Machine-readable version: `scenarios/scenarios.json`. S1-S12 come from the original environment
spec (business traps in the data); S13-S25 are failure-injection scenarios on the main task.

## Verified in a real browser (Chromium via Playwright)

- `popup_banner`: a click on the page underneath fails with Playwright's "intercepts pointer events"
  error until the banner's "Got it" button is clicked. Dismissing reloads the page (typed form input
  is lost); after a re-login the banner returns.
- `drop_connection_once`: the navigation fails with `net::ERR_EMPTY_RESPONSE`. Chromium keeps the
  failed navigation pending: an **immediate** second `goto` to the same URL raised "interrupted by
  another navigation to chrome-error://chromewebdata/", while the same request **0.5 s later**
  returned 200. So `browser_open` should wait about 0.5 s before retrying after a navigation error.
