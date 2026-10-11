"""Deterministic seed for the finance DB. Used by app.py and POST /reset."""
import os
import sqlite3

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE, "data")
DB_PATH = os.path.join(DATA_DIR, "finance.db")

VENDORS = [
    ("V001", "Company X Ltd", "Active"),
    ("V002", "Company X Logistics Pvt Ltd", "Active"),
    ("V003", "Acme Supplies", "Active"),
    ("V004", "Brightline Services", "Active"),
    ("V005", "Nova Stationers", "Active"),
    ("V006", "Delta Packaging", "Inactive"),
    ("V007", "CloudHost Services", "Active"),
    ("V008", "Statutory Auditors LLP", "Active"),
    ("V009", "Zenith Couriers", "Active"),
    ("V010", "Helios IT Solutions", "Active"),
    ("V011", "Patel & Sons Printing", "Active"),
    ("V012", "Greenleaf Facilities", "Active"),
    ("V013", "Sterling Legal Associates", "Active"),
    ("V014", "Redwood Furniture", "Inactive"),
]
# "Orbit Fabricators" is intentionally NOT a vendor.
INVOICES = [
    ("INV-X-1042", "V001", "2026-08-28", 9840000, "2026-09-27", "Paid", "", "", "seed", "2026-08-29"),
    ("INV-AC-778", "V003", "2026-09-15", 2360000, "2026-10-15", "Pending Approval", "", "", "seed", "2026-09-16"),
]
# Extra history: none of these numbers appear in the mail portal (INV-X-1107, INV-XL-0311, INV-BL-2209,
# INV-OF-5001, INV-NS-0042 and CN-X-0009 must stay absent).
INVOICES += [
    ("INV-X-0987", "V001", "2026-07-30", 8260000, "2026-08-29", "Paid", "PO-1180", "July consulting", "seed", "2026-07-31"),
    ("INV-XL-0299", "V002", "2026-08-31", 4425000, "2026-09-30", "Paid", "PO-1204", "", "seed", "2026-09-01"),
    ("INV-AC-731", "V003", "2026-08-14", 1888000, "2026-09-13", "Paid", "", "", "seed", "2026-08-15"),
    ("INV-BL-2154", "V004", "2026-08-20", 3900000, "2026-09-19", "Paid", "PO-1199", "", "seed", "2026-08-21"),
    ("INV-NS-0037", "V005", "2026-08-05", 590000, "2026-08-20", "Paid", "", "Stationery restock", "seed", "2026-08-06"),
    ("INV-DP-0412", "V006", "2026-06-12", 1534000, "2026-07-12", "Paid", "", "Vendor now inactive", "seed", "2026-06-13"),
    ("INV-CH-2610", "V007", "2026-09-26", 1770000, "2026-10-26", "Pending Approval", "PO-1231", "Hosting plan Q4", "seed", "2026-09-27"),
    ("INV-CH-2609", "V007", "2026-08-26", 1770000, "2026-09-25", "Paid", "PO-1231", "Hosting plan Q3", "seed", "2026-08-27"),
    ("INV-SA-0071", "V008", "2026-09-18", 5900000, "2026-10-18", "Approved", "", "Half-year review fee", "seed", "2026-09-19"),
    ("INV-ZC-5530", "V009", "2026-09-29", 236000, "2026-10-14", "Pending Approval", "", "Courier charges Sep", "seed", "2026-09-30"),
    ("INV-ZC-5481", "V009", "2026-08-30", 212400, "2026-09-14", "Paid", "", "Courier charges Aug", "seed", "2026-08-31"),
    ("INV-HI-3302", "V010", "2026-09-22", 7552000, "2026-10-22", "Approved", "PO-1240", "Laptop support contract", "seed", "2026-09-23"),
    ("INV-HI-3257", "V010", "2026-08-22", 7552000, "2026-09-21", "Paid", "PO-1240", "Laptop support contract", "seed", "2026-08-23"),
    ("INV-PP-0888", "V011", "2026-09-08", 354000, "2026-09-23", "Pending Approval", "", "Letterheads and cards", "seed", "2026-09-09"),
    ("INV-GF-1190", "V012", "2026-10-01", 2655000, "2026-10-31", "Pending Approval", "PO-1252", "Housekeeping Sep", "seed", "2026-10-02"),
    ("INV-GF-1161", "V012", "2026-09-01", 2655000, "2026-10-01", "Paid", "PO-1252", "Housekeeping Aug", "seed", "2026-09-02"),
    ("INV-SL-0204", "V013", "2026-09-12", 4130000, "2026-10-12", "Approved", "", "Contract review", "seed", "2026-09-13"),
    ("INV-RF-0316", "V014", "2026-05-20", 6490000, "2026-06-19", "Paid", "", "Vendor now inactive", "seed", "2026-05-21"),
]


def seed():
    os.makedirs(DATA_DIR, exist_ok=True)
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE vendors (vendor_id TEXT PRIMARY KEY, name TEXT, status TEXT)")
    conn.execute("CREATE TABLE invoices (invoice_no TEXT PRIMARY KEY, vendor_id TEXT, invoice_date TEXT,"
                 " amount_paise INTEGER, due_date TEXT, status TEXT, po_number TEXT, notes TEXT,"
                 " created_by TEXT, created_at TEXT)")
    conn.executemany("INSERT INTO vendors VALUES (?,?,?)", VENDORS)
    conn.executemany("INSERT INTO invoices VALUES (?,?,?,?,?,?,?,?,?,?)", INVOICES)
    conn.commit()
    conn.close()


if __name__ == "__main__":
    seed()
    print("seeded", DB_PATH)
