"""Checker: did the step do what it expected? Code first, model only for fuzzy cases."""
from dataclasses import dataclass

from .tools.base import ToolResult

RETRYABLE = {"ERR_HTTP_5XX", "ERR_HTTP_503", "ERR_NAV_FAILED", "ERR_BLANK_PAGE", "ERR_TIMEOUT",
             "ERR_LOGIN_TRANSIENT", "ERR_PDF_UNREADABLE"}
RECOVERABLE = {"ERR_INTERCEPTED": "dismiss_overlay", "ERR_SESSION_EXPIRED": "relogin"}


@dataclass
class Verdict:
    kind: str          # "ok" | "retry" | "recover" | "replan" | "escalate"
    detail: str = ""


def backoff_seconds(retries):
    # wait 1s before the first retry, 2s before the second
    return 2 ** retries


def missing_quotes(result, expect):
    """Words in "double quotes" inside `expect` that the tool output does not contain."""
    # Only quoted text is checked, it is the one part of a free-text expectation that code can test.
    # Anything else is left for the model to judge.
    seen = f"{result.data} {result.evidence}".lower()
    parts = (expect or "").split('"')

    missing = []
    # text between a pair of quotes sits at the odd positions: a "b" c "d" e -> [a, b, c, d, e]
    for position in range(1, len(parts) - 1, 2):
        word = parts[position]
        if word.lower() not in seen:
            missing.append(word)
    return missing


def check(result, expect, retries, max_retries):
    if result.ok:
        missing = missing_quotes(result, expect)
        if len(missing) > 0:
            return Verdict("replan", f"expected {missing} in the result, not found")
        return Verdict("ok")

    code = result.error_code or "ERR_UNKNOWN"

    # popup or expired session: the loop runs the named recovery, then tries the step again
    if code in RECOVERABLE:
        return Verdict("recover", RECOVERABLE[code])

    # nobody to ask (ask_human / request_approval without an oracle): stop, don't loop
    if code == "ERR_NEEDS_HUMAN":
        return Verdict("escalate", "a human decision is needed")

    # temporary problems get retried, but only while there is budget left
    if code in RETRYABLE:
        if retries < max_retries:
            return Verdict("retry", code)
        return Verdict("escalate", f"{code} still failing after {retries} retries")

    # "not found" from finance_get_invoice is a normal answer: the save did not land, so a retry is safe.
    # (a missing page in the browser is ERR_HTTP_404, a different code)
    if code == "ERR_NOT_FOUND":
        return Verdict("ok", "not found")

    # ERR_VALIDATION, ERR_POLICY, ERR_HTTP_404 and anything unknown: the plan needs to change
    return Verdict("replan", code)
