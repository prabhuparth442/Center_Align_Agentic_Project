"""Permission layer. Plain code, not prompt text. Returns a Decision for every proposed action."""
import os
from dataclasses import dataclass
from datetime import date
from urllib.parse import urlparse

from .verifier import parse_to_paise


@dataclass
class Decision:
    verdict: str   # "allow" | "needs_approval" | "block"
    reason: str = ""


# facts that must be in memory, with evidence, before the invoice form may be submitted
SUBMIT_FIELDS = ["invoice_no", "vendor_id", "amount", "invoice_date", "due_date"]

# If a value was not read straight from the document, its evidence starts with one of these
# (e.g. "inferred: Net 30 from invoice date", "guess: Company X Ltd")
WEAK_EVIDENCE = ("inferred", "guess")


def origin_allowed(url, cfg):
    allowed = {urlparse(cfg.mail_url).netloc, urlparse(cfg.finance_url).netloc}
    return urlparse(url).netloc in allowed


def is_submit(action):
    """The only write in this project: clicking Save on the finance new-invoice form."""
    args = action.get("args", {})
    if action.get("tool") != "browser_click":
        return False
    if args.get("app") != "finance":
        return False
    return "submit-invoice" in str(args.get("target", ""))


def check(action, state, cfg):
    """action = {"tool": str, "args": dict}"""
    args = action.get("args", {})

    # "//host/x" counts too, urlparse gives it a host
    for key in ("url", "path"):
        value = str(args.get(key, ""))
        if value.startswith(("http", "//")) and not origin_allowed(value, cfg):
            return Decision("block", f"origin not allowed: {value}")

    if not is_submit(action):
        return Decision("allow")
    return check_submit(state, cfg)


def value_of(state, key):
    fact = state.memory.get(key)
    if fact is None:
        return ""
    return str(fact.value).strip()


def check_submit(state, cfg):
    """The gate in front of the one write in this project (Save on the invoice form). Code, not the prompt,
    decides, so the model cannot talk its way past it. Order matters: hard blocks first, approvals after.
    To debug a "block" in the trace, read the reason: every return below has its own message."""
    memory = state.memory

    # blocks first, approvals after
    missing = []
    for key in SUBMIT_FIELDS:
        if key not in memory:
            missing.append(key)
    if len(missing) > 0:
        return Decision("block", f"cannot submit, missing facts: {missing}")

    # every value must say where it was read. The grounded_check tool is what fills that in.
    no_evidence = []
    for key in SUBMIT_FIELDS:
        if memory[key].evidence.strip() == "":
            no_evidence.append(key)
    if len(no_evidence) > 0:
        return Decision("block", f"no evidence recorded for: {no_evidence}")

    if "credit" in value_of(state, "doc_type").lower():
        return Decision("block", "document is a credit note, not an invoice")

    # Only blocks when we know the vendor is inactive. If vendor_status was never stored we can't
    # tell, and the finance form only lists active vendors anyway.
    if value_of(state, "vendor_status").lower() == "inactive":
        return Decision("block", "vendor is inactive")

    # The loop adds the number to state.submitted after a submit, and removes it again when the
    # read-back says it was not found. So a second submit only happens after a read-back.
    if value_of(state, "invoice_no").upper() in state.submitted:
        return Decision("block", "already submitted, read it back with finance_get_invoice")

    try:
        amount_paise = parse_to_paise(value_of(state, "amount"))
        invoice_day = date.fromisoformat(value_of(state, "invoice_date"))
        due_day = date.fromisoformat(value_of(state, "due_date"))
    except ValueError:
        return Decision("block", "amount or dates could not be read")

    # the request_approval tool stores a fact called "approval" when the answer was yes
    if "approval" in memory:
        return Decision("allow", "approved earlier in this run")

    reasons = []
    weak = []
    for key, fact in memory.items():
        if fact.evidence.lower().startswith(WEAK_EVIDENCE):
            weak.append(key)
    if len(weak) > 0:
        reasons.append(f"guessed or inferred values: {weak}")
    if amount_paise > cfg.approval_threshold_inr * 100:
        reasons.append(f"amount is above {cfg.approval_threshold_inr}")
    # same idea of "today" as the finance app (APP_TODAY), not the real clock
    today = date.fromisoformat(os.environ.get("APP_TODAY", "2026-10-05"))
    if due_day < today:
        reasons.append("invoice is past due")
    if due_day < invoice_day:
        reasons.append("due date is before the invoice date")

    if len(reasons) > 0:
        return Decision("needs_approval", "; ".join(reasons))
    return Decision("allow")
