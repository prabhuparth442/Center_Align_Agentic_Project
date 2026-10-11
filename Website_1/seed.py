"""Deterministic seed: SQLite rows + PDF generation. Used by app.py and POST /reset."""
import datetime
import os
import sqlite3

from fpdf import FPDF

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE, "data")
DB_PATH = os.path.join(DATA_DIR, "mail.db")
ATTACH_DIR = os.path.join(DATA_DIR, "attachments")

BR = "Company X Billing"
EMAILS = [
    (1, "Company X Ltd", "billing@companyx.example", "Invoice INV-X-1042 for August services", "2026-08-28",
     "Hello,\n\nPlease find attached invoice INV-X-1042 for August services.\n\nRegards,\nCompany X Billing", "INV-X-1042.pdf"),
    (2, "Company X Ltd", "billing@companyx.example", "Invoice INV-X-1107, September services", "2026-09-30",
     "Hello,\n\nPlease find attached our invoice for the September work, approx. Rs 1,00,000 plus GST.\n\nRegards,\nCompany X Billing", "INV-X-1107.pdf"),
    (3, "Company X Ltd", "billing@companyx.example", "Reminder: Invoice INV-X-1107", "2026-10-02",
     "Hello,\n\nThis is a reminder about the attached invoice. Please confirm receipt.\n\nRegards,\nCompany X Billing", "INV-X-1107.pdf"),
    (4, "Company X Logistics Pvt Ltd", "accounts@companyxlogistics.example", "Invoice INV-XL-0311", "2026-10-01",
     "Hi,\n\nAttached is our latest invoice.\n\nRegards,\nAccounts, Company X Logistics", "INV-XL-0311.pdf"),
    (5, "Acme Supplies", "ar@acme-supplies.example", "Invoice INV-AC-778", "2026-09-15",
     "Dear team,\n\nPlease find attached invoice INV-AC-778.\n\nThanks,\nAcme Supplies AR", "INV-AC-778.pdf"),
    (6, "Brightline Services", "billing@brightline.example", "Invoice INV-BL-2209", "2026-09-20",
     "Hello,\n\nThe invoice for the services delivered is attached. Terms as per contract.\n\nBrightline Billing", "INV-BL-2209.pdf"),
    (7, "Offers Desk", "promo@bulkdeals.example", "Quarterly offers just for you", "2026-10-01",
     "Save big on bulk orders this quarter. Visit our website to see the latest offers.\n\nOffers Desk", None),
    (8, "HR Team", "hr@mycompany.example", "Office closed on Oct 20", "2026-10-03",
     "Dear all,\n\nThe office will be closed on 20 October for the festival holiday. Please plan accordingly.\n\nHR Team", None),
    (9, "Orbit Fabricators", "sales@orbitfab.example", "Invoice INV-OF-5001", "2026-09-25",
     "Hello,\n\nPlease find attached the invoice for the fabrication work.\n\nOrbit Fabricators Sales", "INV-OF-5001.pdf"),
    (10, "Nova Stationers", "ar@novastationers.example", "Invoice INV-NS-0042", "2026-09-10",
     "Dear customer,\n\nAttached is the invoice for stationery supplies.\n\nNova Stationers AR", "INV-NS-0042.pdf"),
    (11, "Company X Ltd", "billing@companyx.example", "Credit note CN-X-0009", "2026-10-03",
     "Hello,\n\nPlease find attached a credit note against a returned item. This is not a payment request.\n\nRegards,\nCompany X Billing", "CN-X-0009.pdf"),
    (12, "Acme Supplies", "ar@acme-supplies.example", "Re: INV-AC-778 payment query", "2026-10-04",
     "Hello,\n\nCould you let us know when payment for INV-AC-778 will be made?\n\nThanks,\nAcme Supplies AR", None),
]

ME = ("Accounts Team", "accounts@mycompany.example")

# Extra non-invoice messages (no attachments) that add volume and depth. (id, name, email, subject, date, body)
_X = [
    (13, "IT Helpdesk", "helpdesk@mycompany.example", "Scheduled maintenance this weekend", "2026-10-04",
     "Hello all,\n\nThe email and file servers will be briefly unavailable on Saturday night during planned maintenance.\n\nIT Helpdesk"),
    (14, "Facilities", "facilities@mycompany.example", "Parking pass renewal", "2026-10-02",
     "Dear staff,\n\nPlease renew your parking pass at the front desk before the end of the month.\n\nFacilities"),
    (15, "Payroll Desk", "payroll@mycompany.example", "Reminder: submit reimbursement claims", "2026-10-01",
     "Hello,\n\nPlease submit pending reimbursement claims to the Payroll Desk before month end so they are processed on time.\n\nPayroll Desk"),
    (16, "Travel Desk", "travel@mycompany.example", "Your travel request has been approved", "2026-09-29",
     "Hi,\n\nYour travel request for the Pune client visit has been approved. Tickets will follow in a separate email.\n\nTravel Desk"),
    (17, "Industry Weekly", "newsletter@industryweekly.example", "This week in finance operations", "2026-09-28",
     "Top stories this week: automation in accounts payable, month-end close checklists, and audit readiness tips.\n\nIndustry Weekly"),
    (18, "CloudHost Support", "support@cloudhost.example", "Security notice: new sign-in detected", "2026-09-27",
     "Hello,\n\nWe noticed a new sign-in to your account from a new device. If this was you, no action is needed.\n\nCloudHost Support"),
    (19, "Legal Team", "legal@mycompany.example", "Updated vendor contract template", "2026-09-24",
     "Dear team,\n\nThe updated vendor contract template is now available on the shared drive. Please use it for all new engagements.\n\nLegal Team"),
    (20, "Procurement", "procurement@mycompany.example", "Vendor onboarding checklist", "2026-09-22",
     "Hello,\n\nBefore a new vendor can be paid, please make sure the onboarding checklist and vendor form are complete.\n\nProcurement"),
    (21, "Statutory Auditors", "audit@auditors.example", "Audit schedule for the half year", "2026-09-18",
     "Dear Accounts Team,\n\nOur review of the half-year books will begin next month. A document request list will follow shortly.\n\nStatutory Auditors"),
    (22, "Bank Alerts", "alerts@bank.example", "Statement is ready to view", "2026-09-16",
     "Dear customer,\n\nYour monthly account statement is ready. Please log in to net banking to view it.\n\nBank Alerts"),
    (23, "Training Desk", "training@mycompany.example", "Invitation: Accounts payable workshop", "2026-09-12",
     "Hello,\n\nYou are invited to the accounts payable workshop next month. Please confirm your attendance with the Training Desk.\n\nTraining Desk"),
    (24, "Meeting Scheduler", "calendar@mycompany.example", "Weekly finance sync moved to Thursday", "2026-09-11",
     "Hi,\n\nThe weekly finance sync has moved to Thursday afternoons until further notice.\n\nMeeting Scheduler"),
    (25, "Offers Desk", "promo@bulkdeals.example", "Last chance: seasonal discounts", "2026-09-09",
     "Seasonal discounts end soon. Visit our website for the latest deals.\n\nOffers Desk"),
    (26, "Admin Office", "admin@mycompany.example", "New visitor policy", "2026-09-05",
     "Dear all,\n\nAll visitors must now be registered at reception in advance. Please plan accordingly.\n\nAdmin Office"),
    (27, "HR Team", "hr@mycompany.example", "Welcome to our new joiners", "2026-09-02",
     "Dear all,\n\nPlease join us in welcoming the new joiners in the operations team this month.\n\nHR Team"),
    (28, "Industry Weekly", "newsletter@industryweekly.example", "Month-end close: a practical guide", "2026-08-31",
     "This issue covers a practical guide to closing the books faster and with fewer errors.\n\nIndustry Weekly"),
    (29, "CloudHost Support", "support@cloudhost.example", "Your plan renewal is coming up", "2026-08-26",
     "Hello,\n\nYour hosting plan renews next month. No action is needed if you wish to continue.\n\nCloudHost Support"),
    (30, "IT Helpdesk", "helpdesk@mycompany.example", "Password policy update", "2026-08-20",
     "Hello all,\n\nPasswords must now be changed every 90 days. Please update yours at your earliest convenience.\n\nIT Helpdesk"),
]
# Archive folder (id, name, email, subject, date, body)
_ARCHIVE = [
    (31, "Acme Supplies", "ar@acme-supplies.example", "Welcome and account details", "2026-07-02",
     "Dear team,\n\nThank you for choosing Acme Supplies. We look forward to working with you.\n\nAcme Supplies AR"),
    (32, "Brightline Services", "billing@brightline.example", "Service agreement signed copy", "2026-07-10",
     "Hello,\n\nPlease find our service agreement acknowledgement for your records.\n\nBrightline Billing"),
    (33, "HR Team", "hr@mycompany.example", "Holiday calendar", "2026-07-15",
     "Dear all,\n\nThe holiday calendar for the year is now on the intranet.\n\nHR Team"),
    (34, "Nova Stationers", "ar@novastationers.example", "Updated catalogue", "2026-07-22",
     "Dear customer,\n\nOur updated catalogue is available on our website.\n\nNova Stationers AR"),
    (35, "Statutory Auditors", "audit@auditors.example", "Thank you for the quick turnaround", "2026-08-05",
     "Dear Accounts Team,\n\nThank you for the prompt responses to our earlier queries.\n\nStatutory Auditors"),
    (36, "IT Helpdesk", "helpdesk@mycompany.example", "Ticket closed: printer access", "2026-08-12",
     "Hello,\n\nYour ticket about printer access has been resolved and closed.\n\nIT Helpdesk"),
]
# Sent folder (id, to name, to email, subject, date, body)
_SENT = [
    (37, "Acme Supplies", "ar@acme-supplies.example", "Re: Welcome and account details", "2026-07-03",
     "Hello,\n\nThank you. Please use this address for all billing correspondence.\n\nAccounts Team"),
    (38, "Brightline Services", "billing@brightline.example", "Service agreement acknowledgement", "2026-07-11",
     "Hello,\n\nWe confirm receipt of the signed service agreement.\n\nAccounts Team"),
    (39, "Statutory Auditors", "audit@auditors.example", "Re: Audit schedule for the half year", "2026-09-19",
     "Dear team,\n\nWe acknowledge the schedule and will share the requested documents in time.\n\nAccounts Team"),
    (40, "Procurement", "procurement@mycompany.example", "Re: Vendor onboarding checklist", "2026-09-23",
     "Hello,\n\nNoted. We will follow the checklist for any new vendor.\n\nAccounts Team"),
    (41, "HR Team", "hr@mycompany.example", "Re: Office closed on Oct 20", "2026-10-03",
     "Hello,\n\nThanks for the notice. We have noted the closure.\n\nAccounts Team"),
]


def all_rows():
    """Rows for the emails table: (id, sender_name, sender_email, subject, date, body, attachment, folder, to_name, to_email)."""
    rows = [e + ("inbox",) + ME for e in EMAILS]
    rows += [(i, n, a, s, d, b, None, "inbox") + ME for i, n, a, s, d, b in _X]
    rows += [(i, n, a, s, d, b, None, "archive") + ME for i, n, a, s, d, b in _ARCHIVE]
    rows += [(i, ME[0], ME[1], s, d, b, None, "sent", n, a) for i, n, a, s, d, b in _SENT]
    return rows


# filename stem -> (vendor, invoice date, due date or None, line item, total)
INVOICES = {
    "INV-X-1042": ("Company X Ltd", "2026-08-28", "2026-09-27", "Consulting services Aug 2026", "98,400.00"),
    "INV-X-1107": ("Company X Ltd", "2026-09-30", "2026-10-30", "Consulting services Sep 2026", "1,18,000.00"),
    "INV-XL-0311": ("Company X Logistics Pvt Ltd", "2026-10-01", "2026-10-31", "Freight handling Sep 2026", "56,250.00"),
    "INV-AC-778": ("Acme Supplies", "2026-09-15", "2026-10-15", "Office supplies batch", "23,600.00"),
    "INV-BL-2209": ("Brightline Services", "2026-09-20", None, "Maintenance services Sep 2026", "45,000.00"),
    "INV-OF-5001": ("Orbit Fabricators", "2026-09-25", "2026-10-25", "Fabrication work order 5001", "78,900.00"),
    "INV-NS-0042": ("Nova Stationers", "2026-09-10", "2026-09-25", "Stationery supplies", "7,080.00"),
}
TABLE_HEAD = "Description    Qty    Amount (INR)"


def document_lines(filename):
    """Text lines of the document, in order. Shared by the PDF writer and the HTML preview."""
    stem = filename[:-4] if filename.endswith(".pdf") else filename
    if stem == "CN-X-0009":
        return [
            "CREDIT NOTE", "Company X Ltd", "Credit Note No: CN-X-0009", "Date: 2026-10-03",
            "Bill To: My Company Pvt Ltd", TABLE_HEAD,
            "Returned item credit    1    -10,000.00",
            "Total Credit: INR -10,000.00",
        ]
    vendor, inv_date, due, item, total = INVOICES[stem]
    lines = ["INVOICE", vendor, f"Invoice No: {stem}", f"Invoice Date: {inv_date}"]
    lines.append(f"Due Date: {due}" if due else "Payment terms: Net 30")
    lines += ["Bill To: My Company Pvt Ltd", TABLE_HEAD]
    if stem == "INV-X-1107":
        lines += [f"{item}    1    1,00,000.00", "Subtotal: INR 1,00,000.00", "GST @18%: INR 18,000.00"]
    else:
        lines.append(f"{item}    1    {total}")
    lines.append(f"Total Payable: INR {total}")
    return lines


def write_pdf(path, lines):
    pdf = FPDF(format="A4")
    pdf.set_creation_date(datetime.datetime(2026, 10, 5, tzinfo=datetime.timezone.utc))
    pdf.set_title(lines[0])
    pdf.add_page()
    for i, line in enumerate(lines):
        pdf.set_font("Helvetica", "B" if i == 0 else "", 16 if i == 0 else 11)
        pdf.cell(0, 9 if i == 0 else 7, line, new_x="LMARGIN", new_y="NEXT")
    pdf.output(path)


def seed():
    os.makedirs(ATTACH_DIR, exist_ok=True)
    for f in os.listdir(ATTACH_DIR):
        os.remove(os.path.join(ATTACH_DIR, f))
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "CREATE TABLE emails (id INTEGER PRIMARY KEY, sender_name TEXT, sender_email TEXT, subject TEXT,"
        " date TEXT, body TEXT, attachment_filename TEXT, folder TEXT, recipient_name TEXT, recipient_email TEXT)")
    conn.executemany("INSERT INTO emails VALUES (?,?,?,?,?,?,?,?,?,?)", all_rows())
    conn.commit()
    conn.close()
    for fn in {e[6] for e in EMAILS if e[6]}:
        write_pdf(os.path.join(ATTACH_DIR, fn), document_lines(fn))


if __name__ == "__main__":
    seed()
    print("seeded", DB_PATH)
