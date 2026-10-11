"""Website 2: Finance System (mock target system). See README.md."""
import json
import os
import re
import sqlite3
import threading
import time
from datetime import date, datetime

from flask import (Flask, abort, flash, g, jsonify, redirect, render_template, request, session, url_for)

import faults
import seed
from money import format_inr, parse_amount

APP_TODAY = os.environ.get("APP_TODAY", "2026-10-05")
STARTUP_FAULTS = os.environ.get("FAULTS", "")
ADMIN_TOKEN = "eval-token"
USER, PASSWORD = "finance_user", "finance123"
LOG_PATH = os.path.join(seed.DATA_DIR, "request_log.jsonl")

app = Flask(__name__)
app.secret_key = "finance-mock-env-secret"
app.config["SESSION_COOKIE_NAME"] = "finance_session"
app.jinja_env.filters["inr"] = format_inr
app.jinja_env.globals["today_str"] = APP_TODAY

lock = threading.RLock()
state = {"faults": STARTUP_FAULTS, "flags": faults.parse(STARTUP_FAULTS), "error500_fired": [],
         "auth_page_loads": 0, "valid_submits_seen": 0, "first_submit_failed": False, "fired": {}}

OPEN_PATHS = {"/login", "/health", "/reset", "/banner/dismiss"}
COUNTED_ENDPOINTS = {"vendors", "invoices", "invoice_detail", "invoice_new"}
INVOICE_NO_RE = re.compile(r"^[A-Z0-9\-]{3,30}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
AMOUNT_RE = re.compile(r"^\d+(\.\d{1,2})?$")


def db():
    conn = sqlite3.connect(seed.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_seeded():
    if not os.path.exists(seed.DB_PATH):
        seed.seed()


def is_admin_path(path):
    return path.startswith("/admin/")


def has_token():
    return request.headers.get("X-Admin-Token") == ADMIN_TOKEN


def require_admin():
    if not has_token():
        abort(403)


@app.before_request
def before():
    g.fault = None
    path = request.path
    flags = state["flags"]
    if path != "/health" and not is_admin_path(path):
        ms = faults.int_flag(flags, "slow_ms")
        if ms:
            g.fault = "slow_ms"
            time.sleep(ms / 1000.0)
    target = flags.get("error500_once")
    if target and path == target:
        with lock:
            if path not in state["error500_fired"]:
                state["error500_fired"].append(path)
                g.fault = "error500_once"
                return render_template("error500.html"), 500
    resp, name = faults.request_faults(state, lock, request)
    if name:
        g.fault = name
    if resp is not None:
        return resp
    if path in OPEN_PATHS or is_admin_path(path) or path.startswith("/static/"):
        return None
    if not session.get("user"):
        if path.startswith("/api/"):
            return None  # token auth is checked in the API views
        return redirect(url_for("login"))
    limit = faults.int_flag(flags, "session_expires_after")
    if limit is not None and session.get("auth_page_loads", 0) >= limit:
        session.clear()
        g.fault = "session_expires_after"
        return redirect(url_for("login", expired=1))
    if request.method == "GET" and request.endpoint in COUNTED_ENDPOINTS:
        session["auth_page_loads"] = session.get("auth_page_loads", 0) + 1
        state["auth_page_loads"] = session["auth_page_loads"]
    return None


@app.after_request
def after(resp):
    entry = {"ts": datetime.now().isoformat(timespec="milliseconds"), "method": request.method,
             "path": request.path, "status": resp.status_code, "user": session.get("user"),
             "fault_applied": getattr(g, "fault", None)}
    with lock:
        os.makedirs(seed.DATA_DIR, exist_ok=True)
        with open(LOG_PATH, "a") as f:
            f.write(json.dumps(entry) + "\n")
    return resp


@app.errorhandler(404)
def not_found(_e):
    return render_template("not_found.html"), 404


@app.route("/")
def index():
    return redirect(url_for("invoices"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if request.form.get("username") == USER and request.form.get("password") == PASSWORD:
            if "login_flaky_once" in state["flags"] and faults.bump(state, lock, "login_flaky_once"):
                g.fault = "login_flaky_once"
                flash("Sign-in service temporarily unavailable. Please try again.", "error")
                return render_template("login.html")
            session.clear()
            session["user"] = USER
            session["auth_page_loads"] = 0
            state["auth_page_loads"] = 0
            return redirect(url_for("invoices"))
        flash("Invalid username or password", "error")
    elif request.args.get("expired") == "1":
        flash("Your session has expired. Please sign in again.", "error")
    return render_template("login.html")


@app.context_processor
def banner():
    return {"banner_active": "popup_banner" in state["flags"] and not session.get("banner_dismissed")}


@app.route("/banner/dismiss", methods=["POST"])
def banner_dismiss():
    session["banner_dismissed"] = True
    return redirect(request.referrer or url_for("invoices"))


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------- data helpers ----------

INVOICE_SQL = ("SELECT i.*, v.name AS vendor_name FROM invoices i LEFT JOIN vendors v ON v.vendor_id = i.vendor_id")


def invoice_rows(vendor_id=None):
    sql = INVOICE_SQL + (" WHERE i.vendor_id = ?" if vendor_id else "") + " ORDER BY i.invoice_date DESC, i.invoice_no ASC"
    return [dict(r) for r in db().execute(sql, (vendor_id,) if vendor_id else ())]


def get_invoice(invoice_no):
    r = db().execute(INVOICE_SQL + " WHERE UPPER(i.invoice_no) = ?", (invoice_no.upper(),)).fetchone()
    return dict(r) if r else None


def all_vendors():
    return [dict(r) for r in db().execute("SELECT * FROM vendors ORDER BY vendor_id")]


def api_invoice(r):
    return {"invoice_no": r["invoice_no"], "vendor_id": r["vendor_id"], "vendor_name": r["vendor_name"],
            "invoice_date": r["invoice_date"], "amount": r["amount_paise"] / 100.0,
            "amount_paise": r["amount_paise"], "due_date": r["due_date"], "status": r["status"],
            "po_number": r["po_number"], "notes": r["notes"], "created_by": r["created_by"],
            "created_at": r["created_at"]}


# ---------- pages ----------

@app.route("/vendors")
def vendors():
    q = request.args.get("q", "").strip()
    rows = [v for v in all_vendors() if q.lower() in v["name"].lower()]
    return render_template("vendors.html", vendors=rows, q=q)


@app.route("/vendors/<vendor_id>")
def vendor_detail(vendor_id):
    v = next((x for x in all_vendors() if x["vendor_id"].upper() == vendor_id.upper()), None)
    if v is None:
        abort(404)
    invs = invoice_rows(v["vendor_id"])
    return render_template("vendor_detail.html", v=v, invoices=invs, total=sum(i["amount_paise"] for i in invs))


@app.route("/invoices")
def invoices():
    vendor_id = request.args.get("vendor_id", "").strip()
    rows = invoice_rows(vendor_id or None)
    return render_template("invoices.html", invoices=rows, vendors=all_vendors(), selected=vendor_id)


@app.route("/invoices/<invoice_no>")
def invoice_detail(invoice_no):
    inv = get_invoice(invoice_no)
    if inv is None:
        abort(404)
    return render_template("invoice_detail.html", inv=inv)


@app.route("/dashboard")
def dashboard():
    rows = invoice_rows()
    by_status = {}
    for r in rows:
        s = by_status.setdefault(r["status"], {"count": 0, "paise": 0})
        s["count"] += 1
        s["paise"] += r["amount_paise"]
    overdue = [r for r in rows if r["status"] != "Paid" and r["due_date"] < APP_TODAY]
    return render_template("dashboard.html", rows=rows[:5], by_status=by_status, overdue=overdue,
                           active_vendors=sum(1 for v in all_vendors() if v["status"] == "Active"),
                           today=APP_TODAY)


@app.route("/help")
def help_page():
    return render_template("help.html")


def validate(form, vendors_by_id):
    errors = {}
    vid, no = form["vendor_id"], form["invoice_no"]
    if not vid:
        errors["vendor_id"] = "Please select a vendor."
    elif vid not in vendors_by_id or vendors_by_id[vid]["status"] != "Active":
        errors["vendor_id"] = "Selected vendor is not available."

    no_u = no.upper()
    if not no:
        errors["invoice_no"] = "Invoice number is required."
    elif not INVOICE_NO_RE.match(no_u):
        errors["invoice_no"] = "Invoice number must be 3-30 characters: letters, digits and hyphens only."
    elif get_invoice(no_u):
        errors["invoice_no"] = "Invoice number already exists."

    def real_date(text):
        if not DATE_RE.match(text):
            return None
        try:
            return date.fromisoformat(text)
        except ValueError:
            return None

    today = date.fromisoformat(APP_TODAY)
    inv_d = real_date(form["invoice_date"])
    if inv_d is None:
        errors["invoice_date"] = "Invoice date must be a valid date in YYYY-MM-DD format."
    elif inv_d > today:
        errors["invoice_date"] = "Invoice date cannot be in the future."

    amt = form["amount"]
    if not amt:
        errors["amount"] = "Amount is required."
    elif not AMOUNT_RE.match(amt):
        errors["amount"] = "Amount must be a plain number such as 118000.00 (no commas or symbols)."
    else:
        try:
            parse_amount(amt)
        except ValueError:
            errors["amount"] = "Amount must be greater than 0."

    due_d = real_date(form["due_date"])
    if due_d is None:
        errors["due_date"] = "Due date must be a valid date in YYYY-MM-DD format."
    elif inv_d is not None and due_d < inv_d:
        errors["due_date"] = "Due date cannot be before the invoice date."
    elif due_d < today:
        errors["due_date"] = "Due date cannot be in the past."

    if len(form["notes"]) > 500:
        errors["notes"] = "Notes must be 500 characters or fewer."
    return errors


@app.route("/invoices/new", methods=["GET", "POST"])
def invoice_new():
    vendors_all = all_vendors()
    form = {k: "" for k in ("vendor_id", "invoice_no", "invoice_date", "amount", "due_date", "po_number", "notes")}
    errors = {}
    flags = state["flags"]

    def render():
        return render_template("invoice_new.html", form=form, errors=errors,
                               vendors=[v for v in vendors_all if v["status"] == "Active"],
                               rename="rename_label" in flags, move_submit="move_submit_button" in flags)

    if request.method == "GET":
        return render()

    for k in form:
        form[k] = request.form.get(k, "").strip()
    errors = validate(form, {v["vendor_id"]: v for v in vendors_all})
    if errors:
        flash("Please fix the errors below.", "error")
        return render()

    ms = faults.int_flag(flags, "slow_submit_ms")
    if ms:
        g.fault = "slow_submit_ms"
        time.sleep(ms / 1000.0)
    post_save_500 = False
    with lock:
        state["valid_submits_seen"] += 1
        if "first_submit_fails" in flags and not state["first_submit_failed"]:
            state["first_submit_failed"] = True
            g.fault = "first_submit_fails"
            flash("Temporary system error. Please try again.", "error")
            return render()
        if "submit_error500_once" in flags and faults.bump(state, lock, "submit_error500_once"):
            g.fault = "submit_error500_once"
            return render_template("error500.html"), 500
        invoice_no = form["invoice_no"].upper()
        if "silent_save_fail" in flags:
            g.fault = "silent_save_fail"
        else:
            conn = db()
            conn.execute("INSERT INTO invoices VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (invoice_no, form["vendor_id"], form["invoice_date"], parse_amount(form["amount"]),
                          form["due_date"], "Pending Approval", form["po_number"], form["notes"],
                          session["user"], APP_TODAY))
            conn.commit()
            conn.close()
            if "save_then_500_once" in flags and faults.bump(state, lock, "save_then_500_once"):
                g.fault = "save_then_500_once"
                post_save_500 = True  # the record IS saved, but the response is an error
    if post_save_500:
        return render_template("error500.html"), 500
    flash(f"Invoice {invoice_no} saved.", "success")
    return redirect(url_for("invoice_detail", invoice_no=invoice_no))


# ---------- API ----------

def api_guard():
    if session.get("user") or has_token():
        return None
    return jsonify(error="unauthorized"), 401


@app.route("/api/invoices")
def api_invoices():
    return api_guard() or jsonify([api_invoice(r) for r in invoice_rows()])


@app.route("/api/invoices/<invoice_no>")
def api_invoice_one(invoice_no):
    denied = api_guard()
    if denied:
        return denied
    inv = get_invoice(invoice_no)
    return jsonify(api_invoice(inv)) if inv else (jsonify(error="not_found"), 404)


@app.route("/api/vendors")
def api_vendors():
    return api_guard() or jsonify(all_vendors())


# ---------- utility / admin ----------

@app.route("/health")
def health():
    return jsonify(status="ok", app="finance", today=APP_TODAY)


@app.route("/reset", methods=["POST"])
def reset():
    with lock:
        seed.seed()
        state.update(flags=faults.parse(STARTUP_FAULTS), faults=STARTUP_FAULTS, error500_fired=[],
                     auth_page_loads=0, valid_submits_seen=0, first_submit_failed=False, fired={})
    return jsonify(status="reset", app="finance")


@app.route("/admin/state")
def admin_state():
    require_admin()
    return jsonify(app="finance", today=APP_TODAY, vendors=all_vendors(),
                   invoices=[api_invoice(r) for r in invoice_rows()], faults=state["faults"],
                   counters={"auth_page_loads": state["auth_page_loads"],
                             "valid_submits_seen": state["valid_submits_seen"],
                             "error500_fired": list(state["error500_fired"]),
                             "first_submit_failed": state["first_submit_failed"],
                             "fired": dict(state["fired"])})


@app.route("/admin/faults", methods=["POST"])
def admin_faults():
    require_admin()
    text = (request.get_json(silent=True) or {}).get("faults", "")
    state["faults"] = text
    state["flags"] = faults.parse(text)
    return jsonify(status="ok", faults=text)


@app.route("/admin/log")
def admin_log():
    require_admin()
    entries = []
    if os.path.exists(LOG_PATH):
        with open(LOG_PATH) as f:
            entries = [json.loads(line) for line in f if line.strip()]
    return jsonify(entries)


ensure_seeded()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5002)), debug=False, threaded=True)
