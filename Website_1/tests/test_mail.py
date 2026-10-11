import io
import os
import re
import sys

import pdfplumber
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as mailapp  # noqa: E402

ADMIN = {"X-Admin-Token": "eval-token"}


@pytest.fixture
def client():
    mailapp.STARTUP_FAULTS = ""
    c = mailapp.app.test_client()
    assert c.post("/reset").get_json() == {"status": "reset", "app": "mail"}
    return c


@pytest.fixture
def auth(client):
    client.post("/login", data={"username": "demo_user", "password": "demo123"})
    return client


def ids(html):
    return [int(x) for x in re.findall(r'data-testid="email-row-(\d+)"', html)]


def pdf_text(data):
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        return "\n".join(p.extract_text() for p in pdf.pages)


def norm(s):
    return [re.sub(r"\s+", " ", l).strip() for l in s.splitlines() if l.strip()]


def set_faults(c, text):
    assert c.post("/admin/faults", json={"faults": text}, headers=ADMIN).status_code == 200


ORIG_DESC = [12, 11, 8, 3, 7, 4, 2, 9, 6, 5, 10, 1]
ORIG_ASC = [1, 10, 5, 6, 9, 2, 4, 7, 3, 8, 11, 12]


def test_order_and_count(auth):
    emails = auth.get("/admin/state", headers=ADMIN).get_json()["emails"]
    inbox = [e for e in emails if e["folder"] == "inbox"]
    assert len(emails) == 41 and len(inbox) == 30
    desc = ids(auth.get("/inbox").get_data(as_text=True))
    assert desc == [e["id"] for e in sorted(inbox, key=lambda e: (e["date"], e["id"]), reverse=True)]
    asc = ids(auth.get("/inbox?sort=date_asc").get_data(as_text=True))
    assert asc == [e["id"] for e in sorted(inbox, key=lambda e: (e["date"], e["id"]))]
    # the original 12 keep their exact relative order
    assert [i for i in desc if i <= 12] == ORIG_DESC
    assert [i for i in asc if i <= 12] == ORIG_ASC
    assert "30 messages" in auth.get("/inbox").get_data(as_text=True)


def test_folders_and_contacts(auth):
    assert len(ids(auth.get("/folder/sent").get_data(as_text=True))) == 5
    assert len(ids(auth.get("/folder/archive").get_data(as_text=True))) == 6
    assert auth.get("/folder/nope").status_code == 404
    html = auth.get("/contacts").get_data(as_text=True)
    assert 'data-testid="contacts-table"' in html
    page = auth.get("/contact/billing@companyx.example").get_data(as_text=True)
    assert sorted(ids(page)) == [1, 2, 3, 11]
    assert auth.get("/contact/nobody@x.example").status_code == 404
    assert auth.get("/help").status_code == 200
    e = auth.get("/email/12").get_data(as_text=True)
    assert 'data-testid="email-newer"' in e and 'data-testid="email-older"' in e


def test_search(auth):
    html = auth.get("/inbox?q=company x").get_data(as_text=True)
    assert sorted(ids(html)) == [1, 2, 3, 4, 11]
    assert "5 messages" in html
    assert 'data-testid="inbox-empty"' in auth.get("/inbox?q=zzzz").get_data(as_text=True)


def test_auth(client):
    r = client.get("/inbox")
    assert r.status_code == 302 and "/login" in r.headers["Location"]
    assert "Invalid username or password" in client.post(
        "/login", data={"username": "x", "password": "y"}).get_data(as_text=True)
    r = client.post("/login", data={"username": "demo_user", "password": "demo123"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/inbox")
    assert client.get("/email/999").status_code == 404
    assert "session has expired" in client.get("/login?expired=1").get_data(as_text=True)


def test_pdfs(auth):
    for i in (2, 3):
        r = auth.get(f"/email/{i}/attachment.pdf")
        assert r.mimetype == "application/pdf"
        assert 'filename="INV-X-1107.pdf"' in r.headers["Content-Disposition"]
        t = pdf_text(r.data)
        assert "Total Payable: INR 1,18,000.00" in t and "Due Date: 2026-10-30" in t
    t6 = pdf_text(auth.get("/email/6/attachment.pdf").data)
    assert "Payment terms: Net 30" in t6 and "Due Date" not in t6
    assert "CREDIT NOTE" in pdf_text(auth.get("/email/11/attachment.pdf").data)
    assert auth.get("/email/7/attachment.pdf").status_code == 404


def test_preview_matches_pdf(auth):
    for i in (1, 2, 3, 4, 5, 6, 9, 10, 11):
        html = auth.get(f"/email/{i}/attachment").get_data(as_text=True)
        block = re.search(r'data-testid="attachment-text">(.*?)</div>\n?<p>', html, re.S).group(1)
        lines = [re.sub(r"<[^>]+>", " ", l) for l in block.split("</div>")]
        import html as h
        preview = [re.sub(r"\s+", " ", h.unescape(l)).strip() for l in lines if l.strip()]
        assert preview == norm(pdf_text(auth.get(f"/email/{i}/attachment.pdf").data))


def test_no_rupee_glyph_and_distinct_files(auth):
    files = os.listdir(mailapp.seed.ATTACH_DIR)
    assert len(files) == 8
    for f in files:
        assert "₹" not in pdf_text(open(os.path.join(mailapp.seed.ATTACH_DIR, f), "rb").read())


def test_slow_ms(auth):
    import time
    set_faults(auth, "slow_ms=300")
    t = time.time()
    auth.get("/inbox")
    assert time.time() - t >= 0.3
    t = time.time()
    auth.get("/health")
    assert time.time() - t < 0.2


def test_error500_once_and_reset(auth):
    set_faults(auth, "error500_once=/email/2")
    r = auth.get("/email/2")
    assert r.status_code == 500 and "Internal Server Error" in r.get_data(as_text=True)
    assert auth.get("/email/2").status_code == 200
    st = auth.get("/admin/state", headers=ADMIN).get_json()
    assert st["counters"]["error500_fired"] == ["/email/2"]
    auth.post("/reset")
    st = auth.get("/admin/state", headers=ADMIN).get_json()
    assert st["counters"]["error500_fired"] == [] and st["faults"] == ""


def test_session_expires_after(auth):
    set_faults(auth, "session_expires_after=2")
    assert auth.get("/inbox").status_code == 200
    assert auth.get("/email/1/attachment.pdf").status_code == 200  # PDF download is not counted
    assert auth.get("/email/1").status_code == 200
    r = auth.get("/inbox")
    assert r.status_code == 302 and r.headers["Location"].endswith("/login?expired=1")
    assert auth.get("/inbox").status_code == 302  # session cleared
    auth.post("/login", data={"username": "demo_user", "password": "demo123"})
    assert auth.get("/inbox").status_code == 200  # counter reset on login


def test_admin_requires_token(client):
    assert client.get("/admin/state").status_code == 403
    assert client.post("/admin/faults", json={"faults": ""}).status_code == 403
    assert client.get("/admin/log").status_code == 403
    assert client.get("/admin/log", headers=ADMIN).status_code == 200
    assert client.get("/health").get_json() == {"status": "ok", "app": "mail", "today": "2026-10-05"}
