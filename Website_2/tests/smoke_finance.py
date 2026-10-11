"""Playwright smoke: run with the app already up on 5002. Exits 0 on success."""
import os
import sys

import requests
from playwright.sync_api import sync_playwright

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:5002")
HDR = {"X-Admin-Token": "eval-token"}

with sync_playwright() as p:
    b = p.chromium.launch()
    page = b.new_page()
    page.goto(f"{BASE}/login")
    page.get_by_test_id("username").fill("finance_user")
    page.get_by_test_id("password").fill("finance123")
    page.get_by_test_id("login-submit").click()
    page.get_by_test_id("nav-new-invoice").click()
    page.get_by_test_id("field-vendor").select_option(label="Acme Supplies")
    page.get_by_test_id("field-invoice-no").fill("INV-TEST-0001")
    page.get_by_test_id("field-invoice-date").fill("2026-10-01")
    page.get_by_test_id("field-amount").fill("1000.00")
    page.get_by_test_id("field-due-date").fill("2026-11-01")
    page.get_by_test_id("submit-invoice").click()
    assert "Invoice INV-TEST-0001 saved." in page.get_by_test_id("flash").inner_text()
    assert page.get_by_test_id("detail-amount").inner_text() == "1,000.00"
    assert page.get_by_test_id("detail-vendor").inner_text() == "Acme Supplies"
    assert page.get_by_test_id("detail-due_date").inner_text() == "2026-11-01"
    b.close()

r = requests.get(f"{BASE}/api/invoices/INV-TEST-0001", headers=HDR)
assert r.status_code == 200 and r.json()["amount"] == 1000.0, r.text
print("saved:", r.json())
assert requests.post(f"{BASE}/reset").json() == {"status": "reset", "app": "finance"}
assert requests.get(f"{BASE}/api/invoices/INV-TEST-0001", headers=HDR).status_code == 404
print("reset ok, record gone")
sys.exit(0)
