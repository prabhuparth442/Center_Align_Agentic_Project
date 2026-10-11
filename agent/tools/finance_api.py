"""Read-only read-back tools. Independent of the UI path that did the write.

WHY THIS FILE EXISTS
--------------------
After the agent submits an invoice through the web form, how do we know it was really saved?
We CANNOT trust the page: several injected faults make the UI lie or be ambiguous:
  * silent_save_fail   -> shows "Invoice saved" but nothing was stored (false success)
  * save_then_500_once -> shows an error page but the invoice WAS stored (ambiguous failure)
So we ask the system of record a second way: the finance app's JSON API (/api/invoices...).
That is "read-back". It is "independent of the UI path" = it doesn't go through the form/page
that might be lying.

These tools are strictly READ-ONLY (no `writes=True`), so they can be called any time, and the
permission layer requires them before any retry of a submit (the "idempotency" rule: never
re-submit blindly or you create a duplicate invoice).

Status: all three tools are implemented. The TODO comments stay, with a short DONE note under each.
"""
from urllib.parse import quote

from playwright.sync_api import TimeoutError as PlaywrightTimeout

from .base import ToolResult, tool
from .browser import get_session, http_error, first_line


def api_get(path):
    """GET a path of the finance API. Returns a ToolResult, with the decoded JSON as data."""
    # The browser's request client shares its cookies with the browser, so this is logged in
    # after browser_login("finance"). Before that the API answers 401.
    session = get_session()
    try:
        session.start()
        response = session.context.request.get(session.base_urls["finance"] + path)
    except PlaywrightTimeout:
        return ToolResult.fail("ERR_TIMEOUT", "the finance API did not answer in time")
    except Exception as exc:
        return ToolResult.fail("ERR_NAV_FAILED", first_line(exc))

    if response.status == 401:
        return ToolResult.fail("ERR_SESSION_EXPIRED", "finance API says unauthorized, log in again")
    if response.status == 404:
        return ToolResult.fail("ERR_NOT_FOUND", f"{path} does not exist")
    problem = http_error(response.status, response.headers.get("retry-after"), "finance API")
    if problem is not None:
        return problem

    try:
        return ToolResult(True, response.json())
    except Exception:
        return ToolResult.fail("ERR_NAV_FAILED", "finance API did not return JSON")


@tool("finance_get_invoice", "Fetch one invoice record (read-only)", {"invoice_no": "str"})
def finance_get_invoice(invoice_no):
    # The most important read-back: "does invoice X exist now?". Answer decides retry vs. stop.
    # TODO: GET with the logged-in session; ERR_NOT_FOUND if absent.
    #   - "logged-in session": the API rejects anonymous callers (401), so reuse the browser
    #     session/cookies from browser_login rather than inventing separate credentials.
    #   - ERR_NOT_FOUND is a NORMAL, meaningful answer here (not a crash): it tells the agent
    #     the save did not land, so a retry is safe.
    #
    # DONE: GET /api/invoices/<number>. Returns the record as the API sends it, verifier.py compares
    # its fields. 404 -> ERR_NOT_FOUND, 401 -> ERR_SESSION_EXPIRED (log in again).
    number = str(invoice_no).strip()
    if number == "":
        return ToolResult.fail("ERR_VALIDATION", "invoice_no is empty")

    result = api_get("/api/invoices/" + quote(number, safe=""))   # quote() so odd characters can't break the url
    if not result.ok:
        return result

    result.evidence = f"GET /api/invoices/{number} -> found, {result.data.get('amount_paise')} paise"
    return result


@tool("finance_list_invoices", "List invoices, optionally per vendor", {"vendor_id": "str?"})
def finance_list_invoices(vendor_id=None):
    # `vendor_id=None` (the "?" in "str?") means optional: omit it to list everything.
    # Used by the reconciliation task (compare many invoices against expectations), e.g. to
    # detect an existing duplicate for the same vendor before creating a new one.
    #
    # DONE: the API has no vendor filter, so we filter here. Each invoice is cut down to six fields
    # so the list fits in the prompt.
    result = api_get("/api/invoices")
    if not result.ok:
        return result

    short_list = []
    for invoice in result.data:
        if vendor_id and str(invoice.get("vendor_id", "")).lower() != str(vendor_id).strip().lower():
            continue
        short_list.append({
            "invoice_no": invoice.get("invoice_no"),
            "vendor_id": invoice.get("vendor_id"),
            "invoice_date": invoice.get("invoice_date"),
            "due_date": invoice.get("due_date"),
            "amount_paise": invoice.get("amount_paise"),
            "status": invoice.get("status"),
        })

    note = f"{len(short_list)} invoice(s)"
    if vendor_id:
        note = note + f" for {vendor_id}"
    return ToolResult(True, short_list, evidence=note)


@tool("finance_list_vendors", "Search vendors (id, name, status)", {"query": "str?"})
def finance_list_vendors(query=None):
    # Resolve the vendor BEFORE writing: the PDF has a vendor name, the form needs a vendor_id.
    # Returning `status` matters - policy.py blocks writes for missing/inactive vendors, and
    # multiple similar names (ambiguous match) require approval instead of a guess.
    #
    # DONE: keeps the vendors whose name contains `query`, ignoring upper/lower case. More than one
    # result means the name is ambiguous, the agent should ask_human. An empty list = no such vendor.
    result = api_get("/api/vendors")
    if not result.ok:
        return result

    short_list = []
    for vendor in result.data:
        if query and str(query).strip().lower() not in str(vendor.get("name", "")).lower():
            continue
        short_list.append({"vendor_id": vendor["vendor_id"], "name": vendor["name"], "status": vendor["status"]})

    note = f"{len(short_list)} vendor(s)"
    if query:
        note = note + f" matching '{query}'"
    return ToolResult(True, short_list, evidence=note)
