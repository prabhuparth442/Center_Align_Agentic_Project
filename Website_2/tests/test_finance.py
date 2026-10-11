import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as fin  # noqa: E402
import seed as seedmod  # noqa: E402
from money import format_inr, parse_amount  # noqa: E402

ADMIN = {"X-Admin-Token": "eval-token"}
BASE_NOS = [i[0] for i in sorted(seedmod.INVOICES, key=lambda i: (-int(i[2].replace("-", "")), i[0]))]
GOOD = {"vendor_id": "V001", "invoice_no": "inv-x-1107", "invoice_date": "2026-09-30", "amount": "118000.00",
        "due_date": "2026-10-30", "po_number": "", "notes": ""}


@pytest.fixture
def client():
    fin.STARTUP_FAULTS = ""
    c = fin.app.test_client()
    assert c.post("/reset").get_json() == {"status": "reset", "app": "finance"}
    return c


@pytest.fixture
def auth(client):
    client.post("/login", data={"username": "finance_user", "password": "finance123"})
    return client


def text(r):
    return r.get_data(as_text=True)


def set_faults(c, f):
    assert c.post("/admin/faults", json={"faults": f}, headers=ADMIN).status_code == 200


def invoice_nos(c):
    return [i["invoice_no"] for i in c.get("/api/invoices").get_json()]


def test_money():
    assert parse_amount("118000.00") == 11800000 and parse_amount("118000") == 11800000
    assert format_inr(11800000) == "1,18,000.00" and format_inr(9840000) == "98,400.00"
    assert format_inr(708000) == "7,080.00" and format_inr(10000000) == "1,00,000.00"
    for bad in ("1,18,000.00", "Rs 100", "-5", "0", "100.123", ""):
        with pytest.raises(ValueError):
            parse_amount(bad)


def test_seed_and_order(auth):
    st = auth.get("/admin/state", headers=ADMIN).get_json()
    assert len(st["vendors"]) == 14 and len(st["invoices"]) == 20
    assert invoice_nos(auth) == BASE_NOS
    html = text(auth.get("/invoices"))
    assert html.index("invoice-row-INV-AC-778") < html.index("invoice-row-INV-X-1042")
    assert "20 invoices" in html and "98,400.00" in html
    # traps: these must NOT exist before an agent enters them
    for no in ("INV-X-1107", "INV-XL-0311", "INV-BL-2209", "INV-OF-5001", "INV-NS-0042", "CN-X-0009"):
        assert no not in BASE_NOS
    assert not any("orbit" in v["name"].lower() for v in st["vendors"])
    assert "INV-AC-778" in text(auth.get("/invoices?vendor_id=V003")) and "2 invoices" in text(auth.get("/invoices?vendor_id=V003"))


def test_vendor_pages(auth):
    html = text(auth.get("/vendors"))
    assert all(f"vendor-row-V{n:03d}" in html for n in range(1, 15))
    assert "vendor-row-V003" in text(auth.get("/vendors?q=acme")) and "vendors-empty" in text(auth.get("/vendors?q=zzz"))
    assert "Total billed" in text(auth.get("/vendors/V003")) and auth.get("/vendors/V999").status_code == 404
    assert auth.get("/dashboard").status_code == 200 and auth.get("/help").status_code == 200


def test_dropdown(auth):
    form = text(auth.get("/invoices/new"))
    for n in (1, 2, 3, 4, 5, 7, 8, 9, 10, 11, 12, 13):
        assert f'value="V{n:03d}"' in form
    assert 'value="V006"' not in form and 'value="V014"' not in form


CASES = [
    ({"vendor_id": ""}, "vendor_id", "Please select a vendor."),
    ({"vendor_id": "V006"}, "vendor_id", "Selected vendor is not available."),
    ({"vendor_id": "V999"}, "vendor_id", "Selected vendor is not available."),
    ({"invoice_no": ""}, "invoice_no", "Invoice number is required."),
    ({"invoice_no": "A!"}, "invoice_no", "Invoice number must be 3-30 characters: letters, digits and hyphens only."),
    ({"invoice_no": "inv-x-1042"}, "invoice_no", "Invoice number already exists."),
    ({"invoice_date": ""}, "invoice_date", "Invoice date must be a valid date in YYYY-MM-DD format."),
    ({"invoice_date": "2026-02-30"}, "invoice_date", "Invoice date must be a valid date in YYYY-MM-DD format."),
    ({"invoice_date": "2026-10-06"}, "invoice_date", "Invoice date cannot be in the future."),
    ({"amount": ""}, "amount", "Amount is required."),
    ({"amount": "1,18,000.00"}, "amount", "Amount must be a plain number such as 118000.00 (no commas or symbols)."),
    ({"amount": "-5"}, "amount", "Amount must be a plain number such as 118000.00 (no commas or symbols)."),
    ({"amount": "0"}, "amount", "Amount must be greater than 0."),
    ({"due_date": "30/10/2026"}, "due_date", "Due date must be a valid date in YYYY-MM-DD format."),
    ({"due_date": "2026-09-29"}, "due_date", "Due date cannot be before the invoice date."),
    ({"invoice_date": "2026-09-01", "due_date": "2026-09-25"}, "due_date", "Due date cannot be in the past."),
    ({"notes": "x" * 501}, "notes", "Notes must be 500 characters or fewer."),
]


@pytest.mark.parametrize("override,field,msg", CASES)
def test_validation(auth, override, field, msg):
    r = auth.post("/invoices/new", data={**GOOD, **override})
    html = text(r)
    assert r.status_code == 200
    assert re.search(rf'data-testid="error-{field}">{re.escape(msg)}<', html.replace("&#39;", "'"))
    assert "Please fix the errors below." in html
    assert invoice_nos(auth) == BASE_NOS


def test_multiple_errors_and_preserved(auth):
    r = auth.post("/invoices/new", data={**GOOD, "vendor_id": "V003", "amount": "Rs 5", "due_date": "bad", "po_number": "PO-9"})
    html = text(r)
    assert "error-amount" in html and "error-due_date" in html and "error-vendor_id" not in html
    assert 'value="Rs 5"' in html and 'value="PO-9"' in html and re.search(r'value="V003"\s+selected', html)


def test_valid_submit_and_duplicate(auth):
    r = auth.post("/invoices/new", data=GOOD)
    assert r.status_code == 302 and r.headers["Location"].endswith("/invoices/INV-X-1107")
    assert "Invoice INV-X-1107 saved." in text(auth.get("/invoices/INV-X-1107"))
    a = auth.get("/api/invoices/inv-x-1107").get_json()
    assert a["amount"] == 118000.0 and a["status"] == "Pending Approval"
    assert a["created_by"] == "finance_user" and a["created_at"] == "2026-10-05"
    assert "Invoice number already exists." in text(auth.post("/invoices/new", data=GOOD))
    assert auth.get("/invoices/NOPE").status_code == 404
    d = text(auth.get("/invoices/INV-X-1107"))
    assert 'data-testid="detail-amount">1,18,000.00<' in d


def test_past_due_rejected(auth):
    r = auth.post("/invoices/new", data={**GOOD, "invoice_no": "INV-NS-0042", "invoice_date": "2026-09-10",
                                        "due_date": "2026-09-25", "vendor_id": "V005"})
    assert "Due date cannot be in the past." in text(r)


def test_slow_and_500(auth):
    import time
    set_faults(auth, "slow_ms=300")
    t = time.time(); auth.get("/vendors"); assert time.time() - t >= 0.3
    t = time.time(); auth.get("/health"); assert time.time() - t < 0.2
    set_faults(auth, "error500_once=/invoices/new")
    r = auth.get("/invoices/new")
    assert r.status_code == 500 and "Internal Server Error" in text(r)
    assert auth.get("/invoices/new").status_code == 200
    assert auth.get("/admin/state", headers=ADMIN).get_json()["counters"]["error500_fired"] == ["/invoices/new"]
    auth.post("/reset")
    st = auth.get("/admin/state", headers=ADMIN).get_json()
    assert st["counters"]["error500_fired"] == [] and st["faults"] == ""


def test_session_expiry(auth):
    set_faults(auth, "session_expires_after=2")
    assert auth.get("/vendors").status_code == 200
    assert auth.get("/api/invoices").status_code == 200  # not counted
    assert auth.get("/invoices").status_code == 200
    r = auth.get("/invoices")
    assert r.status_code == 302 and r.headers["Location"].endswith("/login?expired=1")
    assert auth.get("/invoices").status_code == 302
    auth.post("/login", data={"username": "finance_user", "password": "finance123"})
    assert auth.get("/invoices").status_code == 200


def test_first_submit_fails(auth):
    set_faults(auth, "first_submit_fails")
    auth.post("/invoices/new", data={**GOOD, "amount": "bad"})  # validation failure: not consumed
    r = auth.post("/invoices/new", data=GOOD)
    assert r.status_code == 200 and "Temporary system error. Please try again." in text(r)
    assert "error-" not in text(r).replace("error-page", "")
    assert invoice_nos(auth) == BASE_NOS
    assert auth.get("/admin/state", headers=ADMIN).get_json()["counters"]["first_submit_failed"] is True
    assert auth.post("/invoices/new", data=GOOD).status_code == 302
    assert "INV-X-1107" in invoice_nos(auth)
    auth.post("/reset")
    assert auth.get("/admin/state", headers=ADMIN).get_json()["counters"]["first_submit_failed"] is False


def test_rename_label_and_move_button(auth):
    set_faults(auth, "rename_label,move_submit_button")
    html = text(auth.get("/invoices/new"))
    assert ">Total Payable (INR)<" in html and ">Payment Due By<" in html
    assert 'name="amount"' in html and 'data-testid="field-due-date"' in html
    assert html.index("submit-invoice") < html.index("field-vendor")
    set_faults(auth, "")
    html = text(auth.get("/invoices/new"))
    assert ">Invoice Amount<" in html and html.index("submit-invoice") > html.index("field-notes")


def test_silent_save_fail(auth):
    set_faults(auth, "silent_save_fail")
    r = auth.post("/invoices/new", data=GOOD, follow_redirects=True)
    assert r.status_code == 404
    assert "INV-X-1107" not in invoice_nos(auth)
    st = auth.get("/admin/state", headers=ADMIN).get_json()
    assert st["counters"]["valid_submits_seen"] == 1 and len(st["invoices"]) == 20
    r = auth.post("/invoices/new", data=GOOD)
    assert r.status_code == 302
    flashes = auth.get("/vendors")
    assert "Invoice INV-X-1107 saved." in text(flashes)


def test_auth_guards(client):
    assert client.get("/invoices").status_code == 302
    assert client.get("/api/invoices").status_code == 401
    assert client.get("/api/invoices").get_json() == {"error": "unauthorized"}
    assert client.get("/api/invoices", headers=ADMIN).status_code == 200
    assert client.get("/api/invoices/NOPE", headers=ADMIN).get_json() == {"error": "not_found"}
    assert client.get("/admin/state").status_code == 403
    assert client.get("/health").get_json() == {"status": "ok", "app": "finance", "today": "2026-10-05"}
    assert "Invalid username or password" in text(client.post("/login", data={"username": "a", "password": "b"}))
    assert "session has expired" in text(client.get("/login?expired=1"))
