"""DRAFT of agent/tools/finance_api.py with all three tools written out in plain, readable Python.
Read it top to bottom, then copy what you understand into finance_api.py. Delete this file afterwards.

Nothing imports this file. It is only here to learn from.

=====================================================================================================
WHAT THIS FILE IS FOR
=====================================================================================================
After the agent fills in the finance web form and clicks Save, how do we know the invoice was REALLY
saved? We cannot trust the page. Two of the faults we inject make the page lie:

    silent_save_fail    the page says "Invoice saved" but nothing was stored   (false success)
    save_then_500_once  the page shows an error but the invoice WAS stored     (false failure)

So we ask the finance system a SECOND way that does not go through the form: its JSON API,
    GET /api/invoices/<invoice_no>    one invoice
    GET /api/invoices                 all invoices
    GET /api/vendors                  all vendors
This is called READ-BACK. It is the "independent" part: the answer comes from the system of record, not
from the page that might be lying.

The three tools here only READ. They never create, change or delete anything, so it is always safe to
call them. policy.py even REQUIRES a read-back before a submit may be tried a second time (otherwise a
retry could create a duplicate invoice).

    finance_get_invoice    one invoice by number      "does it exist now, and what does it contain?"
    finance_list_invoices  many invoices              "is there already one for this vendor?"
    finance_list_vendors   find a vendor              "which vendor_id belongs to 'Company X Ltd'?"

=====================================================================================================
HOW WE LOG IN TO THE API
=====================================================================================================
The API refuses anonymous callers (HTTP 401). We do NOT invent a second set of credentials. The browser
and our HTTP requests share ONE cookie jar (the browser "context"), so once browser_login("finance") has
run, these requests are logged in too. Before that, the API answers 401, and we turn that into
ERR_SESSION_EXPIRED so the loop knows to log in again.
"""
from urllib.parse import quote

from .base import ToolResult, tool
from .browser import get_session, http_error

# Same safe import as in browser.py: if Playwright is missing, importing this file must still work.
try:
    from playwright.sync_api import TimeoutError as PlaywrightTimeout
except ImportError:
    PlaywrightTimeout = TimeoutError


def api_get(path):
    """GET <finance address><path> and return a ToolResult.

    On success the result's data is the decoded JSON (a dict or a list). On any problem it is an error
    result with one of our stable codes. All three tools share this, so the error handling is written
    only once and the tools below stay short.
    """
    session = get_session()
    try:
        session.start()                                    # make sure the browser (and its cookies) exist
        response = session.context.request.get(session.base_urls["finance"] + path)
    except PlaywrightTimeout:
        return ToolResult.fail("ERR_TIMEOUT", "the finance API did not answer in time")
    except Exception as error:
        return ToolResult.fail("ERR_NAV_FAILED", str(error).splitlines()[0][:200])

    if response.status == 401:
        return ToolResult.fail("ERR_SESSION_EXPIRED", "finance API says unauthorized, log in again")
    if response.status == 404:
        # For the API this means "no such record". It is a NORMAL answer, not a crash. checker.py treats
        # ERR_NOT_FOUND as fine, because for finance_get_invoice it tells the agent the save did not land.
        return ToolResult.fail("ERR_NOT_FOUND", f"{path} does not exist")
    problem = http_error(response.status, response.headers.get("retry-after"), "finance API")
    if problem is not None:
        return problem                                     # 500, 503 and so on

    try:
        return ToolResult(True, response.json())
    except Exception:
        return ToolResult.fail("ERR_NAV_FAILED", "finance API did not return JSON")


@tool("finance_get_invoice", "Fetch one invoice record (read-only)", {"invoice_no": "str"})
def finance_get_invoice(invoice_no):
    """The most important read-back: "does invoice X exist now?"

    Success: the record exactly as the API gives it (invoice_no, vendor_id, vendor_name, invoice_date,
    due_date, amount, amount_paise, status...). verifier.py compares these fields with what the agent
    extracted from the PDF.
    ERR_NOT_FOUND: it does not exist. The answer that decides "retrying is safe".
    """
    number = str(invoice_no).strip()
    if number == "":
        return ToolResult.fail("ERR_VALIDATION", "invoice_no is empty")

    # quote() makes the number safe to put in a web address (a "/" or a space in it would break the path).
    # The server looks invoice numbers up ignoring upper / lower case, so we do not need to change it.
    result = api_get("/api/invoices/" + quote(number, safe=""))
    if not result.ok:
        return result                                      # pass the error straight on

    record = result.data
    result.evidence = f"GET /api/invoices/{number} -> found, {record.get('amount_paise')} paise"
    return result


@tool("finance_list_invoices", "List invoices, optionally per vendor", {"vendor_id": "str?"})
def finance_list_invoices(vendor_id=None):
    """List invoices, all of them or only one vendor's. "str?" in the tool table means the argument is optional.

    Used to spot an existing invoice for the same vendor before creating a new one.
    The API has no vendor filter, so we filter here. Each invoice is cut down to six fields so that the
    whole list still fits comfortably in the model's prompt.
    """
    result = api_get("/api/invoices")
    if not result.ok:
        return result

    invoices = result.data
    if vendor_id:
        wanted = str(vendor_id).strip().lower()
        invoices = [invoice for invoice in invoices if str(invoice.get("vendor_id", "")).lower() == wanted]

    short_list = []
    for invoice in invoices:
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
    """Find vendors by name. The PDF only has a vendor NAME, but the finance form needs a vendor_id.

    Every vendor comes back with its status (Active / Inactive). policy.py refuses to write for an
    inactive vendor. If MORE THAN ONE vendor matches, the name is ambiguous ("Company X" matches both
    "Company X Ltd" and "Company X Logistics Pvt Ltd"). The agent must ask a human then, never guess.
    An empty list means that vendor does not exist.
    """
    result = api_get("/api/vendors")
    if not result.ok:
        return result

    vendors = result.data
    if query:
        needle = str(query).strip().lower()
        vendors = [vendor for vendor in vendors if needle in str(vendor.get("name", "")).lower()]

    short_list = []
    for vendor in vendors:
        short_list.append({"vendor_id": vendor["vendor_id"], "name": vendor["name"], "status": vendor["status"]})

    note = f"{len(short_list)} vendor(s)"
    if query:
        note = note + f" matching '{query}'"
    return ToolResult(True, short_list, evidence=note)
