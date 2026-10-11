"""Tests for the extended failure-injection flags (see FAILURE_INJECTION.md at the project root)."""
import io
import os
import sys
import threading
import time

import pdfplumber
import pytest
import requests
from werkzeug.serving import make_server

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as mailapp  # noqa: E402

ADMIN = {"X-Admin-Token": "eval-token"}


@pytest.fixture
def client():
    mailapp.STARTUP_FAULTS = ""
    c = mailapp.app.test_client()
    assert c.post("/reset").status_code == 200
    return c


@pytest.fixture
def auth(client):
    client.post("/login", data={"username": "demo_user", "password": "demo123"})
    return client


def set_faults(c, text):
    assert c.post("/admin/faults", json={"faults": text}, headers=ADMIN).status_code == 200


def fired(c):
    return c.get("/admin/state", headers=ADMIN).get_json()["counters"]["fired"]


def test_slow_path_only_slows_that_path(auth):
    set_faults(auth, "slow_path=/help:400")
    t = time.time()
    assert auth.get("/help").status_code == 200
    assert time.time() - t >= 0.4
    t = time.time()
    assert auth.get("/contacts").status_code == 200
    assert time.time() - t < 0.3


def test_error503_once(auth):
    set_faults(auth, "error503_once=/inbox")
    r = auth.get("/inbox")
    assert r.status_code == 503 and r.headers["Retry-After"] == "1"
    assert 'data-testid="error-page"' in r.get_data(as_text=True)
    assert auth.get("/inbox").status_code == 200


def test_fail_first_k(auth):
    set_faults(auth, "fail_first=/inbox:2")
    assert [auth.get("/inbox").status_code for _ in range(4)] == [500, 500, 200, 200]


def test_blank_page_once(auth):
    set_faults(auth, "blank_page_once=/inbox")
    r = auth.get("/inbox")
    assert r.status_code == 200 and "inbox-table" not in r.get_data(as_text=True)
    assert "inbox-table" in auth.get("/inbox").get_data(as_text=True)


def test_drop_connection_fallback_without_socket(auth):
    set_faults(auth, "drop_connection_once=/inbox")
    assert auth.get("/inbox").status_code == 502  # test client has no socket to close
    assert auth.get("/inbox").status_code == 200


def test_drop_connection_on_real_socket():
    mailapp.STARTUP_FAULTS = ""
    mailapp.app.test_client().post("/reset")
    srv = make_server("127.0.0.1", 0, mailapp.app, threaded=True)
    port = srv.server_port
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{port}"
        assert requests.post(base + "/admin/faults", json={"faults": "drop_connection_once=/login"},
                             headers=ADMIN).status_code == 200
        with pytest.raises(requests.exceptions.RequestException):
            requests.get(base + "/login", timeout=5)
        assert requests.get(base + "/login", timeout=5).status_code == 200  # only once
    finally:
        srv.shutdown()
        mailapp.app.test_client().post("/reset")


def test_login_flaky_once(client):
    set_faults(client, "login_flaky_once")
    creds = {"username": "demo_user", "password": "demo123"}
    r = client.post("/login", data=creds)
    assert r.status_code == 200 and "temporarily unavailable" in r.get_data(as_text=True)
    assert client.get("/inbox").status_code == 302  # still logged out
    assert client.post("/login", data=creds).status_code == 302
    assert client.get("/inbox").status_code == 200


def test_login_flaky_does_not_affect_wrong_password(client):
    set_faults(client, "login_flaky_once")
    r = client.post("/login", data={"username": "demo_user", "password": "nope"})
    assert "Invalid username or password" in r.get_data(as_text=True)
    assert fired(client) == {}


def test_popup_banner(auth):
    assert "maintenance-banner" not in auth.get("/inbox").get_data(as_text=True)
    set_faults(auth, "popup_banner")
    html = auth.get("/inbox").get_data(as_text=True)
    assert "maintenance-banner" in html and "banner-dismiss" in html
    r = auth.post("/banner/dismiss", headers={"Referer": "http://localhost/inbox"})
    assert r.status_code == 302
    assert "maintenance-banner" not in auth.get("/inbox").get_data(as_text=True)


def test_popup_banner_returns_after_relogin(client):
    set_faults(client, "popup_banner")
    client.post("/login", data={"username": "demo_user", "password": "demo123"})
    client.post("/banner/dismiss")
    assert "maintenance-banner" not in client.get("/inbox").get_data(as_text=True)
    client.post("/logout")
    client.post("/login", data={"username": "demo_user", "password": "demo123"})
    assert "maintenance-banner" in client.get("/inbox").get_data(as_text=True)


def test_pdf_corrupt_once(auth):
    set_faults(auth, "pdf_corrupt_once")
    first = auth.get("/email/2/attachment.pdf")
    assert first.status_code == 200
    with pytest.raises(Exception):
        with pdfplumber.open(io.BytesIO(first.data)) as pdf:
            "".join(p.extract_text() or "" for p in pdf.pages)
    second = auth.get("/email/2/attachment.pdf")
    with pdfplumber.open(io.BytesIO(second.data)) as pdf:
        assert "1,18,000.00" in "\n".join(p.extract_text() for p in pdf.pages)


def test_pdf_missing_once(auth):
    set_faults(auth, "pdf_missing_once")
    assert auth.get("/email/2/attachment.pdf").status_code == 404
    assert auth.get("/email/2/attachment.pdf").status_code == 200


def test_fired_counters_and_reset(auth):
    set_faults(auth, "error503_once=/inbox")
    auth.get("/inbox")
    assert fired(auth) == {"error503_once": 1}
    auth.post("/reset")
    assert fired(auth) == {}
    auth.post("/login", data={"username": "demo_user", "password": "demo123"})
    set_faults(auth, "error503_once=/inbox")
    assert auth.get("/inbox").status_code == 503  # fires again after reset


def test_unknown_flag_ignored(auth):
    set_faults(auth, "not_a_flag=1")
    assert auth.get("/inbox").status_code == 200
