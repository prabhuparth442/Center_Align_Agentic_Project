"""Tests for the extended failure-injection flags (see FAILURE_INJECTION.md at the project root)."""
import os
import sys
import threading
import time

import pytest
import requests
from werkzeug.serving import make_server

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as fin  # noqa: E402

ADMIN = {"X-Admin-Token": "eval-token"}
GOOD = {"vendor_id": "V001", "invoice_no": "inv-x-1107", "invoice_date": "2026-09-30", "amount": "118000.00",
        "due_date": "2026-10-30", "po_number": "", "notes": ""}


@pytest.fixture
def client():
    fin.STARTUP_FAULTS = ""
    c = fin.app.test_client()
    assert c.post("/reset").status_code == 200
    return c


@pytest.fixture
def auth(client):
    client.post("/login", data={"username": "finance_user", "password": "finance123"})
    return client


def set_faults(c, text):
    assert c.post("/admin/faults", json={"faults": text}, headers=ADMIN).status_code == 200


def state(c):
    return c.get("/admin/state", headers=ADMIN).get_json()


def has_invoice(c, no="INV-X-1107"):
    return c.get(f"/api/invoices/{no}", headers=ADMIN).status_code == 200


def test_slow_path_only_slows_that_path(auth):
    set_faults(auth, "slow_path=/vendors:400")
    t = time.time()
    assert auth.get("/vendors").status_code == 200
    assert time.time() - t >= 0.4
    t = time.time()
    assert auth.get("/invoices").status_code == 200
    assert time.time() - t < 0.3


def test_error503_once(auth):
    set_faults(auth, "error503_once=/invoices")
    r = auth.get("/invoices")
    assert r.status_code == 503 and r.headers["Retry-After"] == "1"
    assert auth.get("/invoices").status_code == 200


def test_fail_first_k(auth):
    set_faults(auth, "fail_first=/invoices:3")
    assert [auth.get("/invoices").status_code for _ in range(5)] == [500, 500, 500, 200, 200]


def test_blank_page_once(auth):
    set_faults(auth, "blank_page_once=/invoices/new")
    r = auth.get("/invoices/new")
    assert r.status_code == 200 and "invoice-form" not in r.get_data(as_text=True)
    assert "invoice-form" in auth.get("/invoices/new").get_data(as_text=True)


def test_drop_connection_fallback_without_socket(auth):
    set_faults(auth, "drop_connection_once=/invoices")
    assert auth.get("/invoices").status_code == 502
    assert auth.get("/invoices").status_code == 200


def test_drop_connection_on_real_socket():
    fin.STARTUP_FAULTS = ""
    fin.app.test_client().post("/reset")
    srv = make_server("127.0.0.1", 0, fin.app, threaded=True)
    port = srv.server_port
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{port}"
        assert requests.post(base + "/admin/faults", json={"faults": "drop_connection_once=/login"},
                             headers=ADMIN).status_code == 200
        with pytest.raises(requests.exceptions.RequestException):
            requests.get(base + "/login", timeout=5)
        assert requests.get(base + "/login", timeout=5).status_code == 200
    finally:
        srv.shutdown()
        fin.app.test_client().post("/reset")


def test_login_flaky_once(client):
    set_faults(client, "login_flaky_once")
    creds = {"username": "finance_user", "password": "finance123"}
    r = client.post("/login", data=creds)
    assert r.status_code == 200 and "temporarily unavailable" in r.get_data(as_text=True)
    assert client.get("/invoices").status_code == 302
    assert client.post("/login", data=creds).status_code == 302
    assert client.get("/invoices").status_code == 200


def test_popup_banner(auth):
    set_faults(auth, "popup_banner")
    html = auth.get("/invoices").get_data(as_text=True)
    assert "maintenance-banner" in html and "banner-dismiss" in html
    assert auth.post("/banner/dismiss", headers={"Referer": "http://localhost/invoices"}).status_code == 302
    assert "maintenance-banner" not in auth.get("/invoices").get_data(as_text=True)


def test_popup_banner_on_login_page(client):
    set_faults(client, "popup_banner")
    assert "maintenance-banner" in client.get("/login").get_data(as_text=True)


def test_slow_submit_ms_only_slows_valid_submit(auth):
    set_faults(auth, "slow_submit_ms=400")
    t = time.time()
    auth.post("/invoices/new", data=dict(GOOD, amount="bad"))  # validation error: not delayed
    assert time.time() - t < 0.3
    t = time.time()
    r = auth.post("/invoices/new", data=GOOD)
    assert time.time() - t >= 0.4 and r.status_code == 302
    assert has_invoice(auth)


def test_submit_error500_once_saves_nothing(auth):
    set_faults(auth, "submit_error500_once")
    r = auth.post("/invoices/new", data=GOOD)
    assert r.status_code == 500 and not has_invoice(auth)
    assert auth.post("/invoices/new", data=GOOD).status_code == 302
    assert has_invoice(auth)


def test_submit_error500_not_consumed_by_validation_error(auth):
    set_faults(auth, "submit_error500_once")
    auth.post("/invoices/new", data=dict(GOOD, amount="1,000"))
    assert state(auth)["counters"]["fired"] == {}
    assert auth.post("/invoices/new", data=GOOD).status_code == 500


def test_save_then_500_once_saves_but_errors(auth):
    set_faults(auth, "save_then_500_once")
    r = auth.post("/invoices/new", data=GOOD)
    assert r.status_code == 500
    assert has_invoice(auth)  # the record IS saved: retrying blindly creates a duplicate error
    retry = auth.post("/invoices/new", data=GOOD)
    assert retry.status_code == 200 and "Invoice number already exists." in retry.get_data(as_text=True)
    assert state(auth)["counters"]["fired"] == {"save_then_500_once": 1}


def test_reset_clears_fired(auth):
    set_faults(auth, "error503_once=/invoices")
    auth.get("/invoices")
    assert state(auth)["counters"]["fired"] == {"error503_once": 1}
    auth.post("/reset")
    auth.post("/login", data={"username": "finance_user", "password": "finance123"})
    assert state(auth)["counters"]["fired"] == {}
