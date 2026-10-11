"""Verifier: independent read-back after the write. Only a pass allows finish(success)."""
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation


@dataclass
class VerifyReport:
    passed: bool
    mismatches: list = field(default_factory=list)


def parse_to_paise(raw_amount):
    """'1,18,000.50' or 118000.5 -> 11800050 (integer paise)."""
    clean = re.sub(r"[^\d.]", "", str(raw_amount))
    try:
        return int(Decimal(clean) * 100)
    except InvalidOperation:
        # Decimal raises its own error type for text like "abc" or "". Callers catch ValueError.
        raise ValueError(f"not an amount: {raw_amount}")


def fetch_remote_record(invoice_no):
    """Read the invoice back from the finance app (API, not the form page).
    Returns the API dict: invoice_no, vendor_id, invoice_date, due_date, amount_paise.
    Raises if the read-back fails, including ERR_NOT_FOUND, which means the save did not land."""
    from .tools.finance_api import finance_get_invoice   # imported here, the tools import this module too
    result = finance_get_invoice(invoice_no)
    if not result.ok:
        raise LookupError(f"{result.error_code}: {result.error_message}")
    return result.data


def verify_invoice(state, trace=None):
    """Read the saved invoice back and compare it with state.memory. Logs to the trace.
    TODO: also open the detail page as a second check."""
    required = ["invoice_no", "vendor_id", "amount", "invoice_date", "due_date"]
    missing = []
    for key in required:
        if key not in state.memory:
            missing.append(key)
    if len(missing) > 0:
        return finish_report(state, trace, [f"missing memory keys: {missing}"])

    invoice_no = state.memory["invoice_no"].value
    try:
        remote = fetch_remote_record(invoice_no)
    except Exception as exc:
        return finish_report(state, trace, [f"could not read invoice back: {exc}"])

    mismatches = []

    for key in ("invoice_no", "vendor_id"):
        want = str(state.memory[key].value).strip().lower()
        got = str(remote.get(key, "")).strip().lower()
        if want != got:
            mismatches.append(f"{key}: expected '{want}', got '{got}'")

    # compare in paise, so floats like 118000.0 can't cause rounding trouble
    try:
        want = parse_to_paise(state.memory["amount"].value)
        got = remote.get("amount_paise")
        if want != got:
            mismatches.append(f"amount: expected {want} paise, got {got}")
    except Exception as exc:
        mismatches.append(f"amount could not be compared: {exc}")

    for key in ("invoice_date", "due_date"):
        want = str(state.memory[key].value).strip()
        got = str(remote.get(key, "")).strip()
        if want != got:
            mismatches.append(f"{key}: expected '{want}', got '{got}'")

    return finish_report(state, trace, mismatches, remote)


def finish_report(state, trace, mismatches, remote=None):
    """Build the report, set state.verified and write the trace line."""
    report = VerifyReport(passed=(len(mismatches) == 0), mismatches=mismatches)
    state.verified = report.passed

    # finish() can call the verifier without a trace
    if trace is not None:
        expected = {}
        for key, fact in state.memory.items():
            expected[key] = fact.value
        trace.log("verify", passed=report.passed, mismatches=mismatches, expected=expected, actual=remote)
    return report
