# Workflow

Model: **DeepSeek V4 Flash** (one model, two modes). Planner = reasoning on, executor/verifier = reasoning off.
V4 Flash is text-only, so the agent reads the DOM (snapshot), not screenshots. Actions are JSON validated by
pydantic; native tool-calling is not relied on (unverified).

## 1. The loop

```mermaid
flowchart TD
    G[Goal] --> P[Planner<br/>thinking ON<br/>steps + expected outcome]
    P --> E[Executor<br/>thinking OFF<br/>one JSON tool call]
    E --> POL{Policy<br/>code}
    POL -- block --> E
    POL -- needs approval --> AP[request_approval / ask_human]
    AP --> E
    POL -- allow --> T[Tool runs<br/>browser / pdf / read-back]
    T --> C{Checker<br/>code first}
    C -- ok, more steps --> E
    C -- transient error --> R[retry with backoff]
    R --> E
    C -- overlay / session expired --> REC[recover: dismiss / re-login]
    REC --> E
    C -- plan is wrong --> RP[Replan<br/>keep done work]
    RP --> E
    C -- budget exhausted --> H[finish: needs_human]
    C -- all steps done --> V{Verifier<br/>independent read-back}
    V -- fields match --> S[finish: success + evidence]
    V -- mismatch / missing --> RP
    S --> TR[(Audit trace + report)]
    H --> TR
```

## 2. Main task (happy path + one ambiguous failure)

```mermaid
sequenceDiagram
    participant A as Agent
    participant M as Mail app
    participant F as Finance app
    A->>M: browser_login, open /inbox, search "Company X Ltd"
    A->>A: model picks newest real invoice (not credit note / resend)
    A->>M: browser_download PDF
    A->>A: read_pdf, propose fields, grounded_check each, remember(+evidence)
    A->>F: finance_list_vendors, finance_get_invoice (exists already?)
    A->>A: policy: approval needed? (inferred / large / past due)
    A->>F: login, open /invoices/new, fill fields, submit
    F-->>A: HTTP 500 (save_then_500_once)
    A->>F: finance_get_invoice (read back BEFORE retrying)
    F-->>A: record exists
    A->>A: verifier compares every field
    A->>A: finish(success, quotes + record + trace path)
```

## 3. Fault to recovery

| Injected fault | Seen as | Reaction |
|---|---|---|
| error500/503, blank page, dropped connection | ERR_HTTP_5XX / ERR_BLANK_PAGE / ERR_NAV_FAILED | wait, retry up to MAX_RETRIES |
| popup_banner | ERR_INTERCEPTED | click `banner-dismiss`, continue |
| session_expires_after | ERR_SESSION_EXPIRED | browser_login, resume the step |
| login_flaky_once | ERR_LOGIN_TRANSIENT | retry login |
| pdf_corrupt_once / pdf_missing_once | ERR_PDF_UNREADABLE / 404 | re-download, else ask_human |
| save_then_500_once / slow_submit_ms | error or timeout on submit | read back first; do not resubmit if record exists |
| silent_save_fail | success banner, read-back empty | verifier fails, report failure honestly |
| fail_first=/inbox:99 | repeated failures | stop, write nothing, needs_human |

## 4. Who decides what

| Decision | Made by |
|---|---|
| Which email/invoice is the right one, how to fill the form | model |
| Is the value really in the document | code (`grounded_check`) |
| Is this action allowed / needs approval | code (`policy.py`) |
| Did the step work, retry or replan | code (`checker.py`), model only for fuzzy cases |
| Did the write really land | code (`verifier.py`) via read-back |
