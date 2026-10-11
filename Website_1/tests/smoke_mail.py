"""Playwright smoke: run with the app already up on 5001. Exits 0 on success."""
import io
import os
import re
import sys

import pdfplumber
from playwright.sync_api import sync_playwright

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:5001")

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(accept_downloads=True)
    page.goto(f"{BASE}/login")
    page.get_by_test_id("username").fill("demo_user")
    page.get_by_test_id("password").fill("demo123")
    page.get_by_test_id("login-submit").click()
    page.get_by_test_id("search-input").fill("Company X Ltd")
    page.get_by_test_id("search-submit").click()
    rows = page.locator('[data-testid^="email-row-"]')
    target = None
    for i in range(rows.count()):  # rows are newest first
        row = rows.nth(i)
        subject = row.locator('[data-testid^="email-link-"]').inner_text()
        if subject.startswith("Invoice") and "Company X Ltd <" in row.inner_text() and "Logistics" not in row.inner_text():
            target = row
            break
    assert target is not None, "no matching email"
    target.locator('[data-testid^="email-link-"]').click()
    with page.expect_download() as dl:
        page.get_by_test_id("attachment-link").click()
    path = dl.value.path()
    with pdfplumber.open(path) as pdf:
        text = "\n".join(pg.extract_text() for pg in pdf.pages)
    browser.close()

amount = re.search(r"Total Payable: INR ([\d,.]+)", text).group(1)
due = re.search(r"Due Date: (\d{4}-\d{2}-\d{2})", text).group(1)
inv = re.search(r"Invoice No: (\S+)", text).group(1)
print(f"invoice={inv} amount={amount} due={due}")
assert amount == "1,18,000.00" and due == "2026-10-30"
sys.exit(0)
