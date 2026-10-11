"""Website 1: Mail & Documents Portal (mock source system). See README.md."""
import json
import os
import sqlite3
import threading
import time
from datetime import datetime

from flask import (Flask, Response, abort, flash, g, jsonify, redirect, render_template, request,
                   send_file, session, url_for)

import faults
import seed

APP_TODAY = os.environ.get("APP_TODAY", "2026-10-05")
STARTUP_FAULTS = os.environ.get("FAULTS", "")
ADMIN_TOKEN = "eval-token"
USER, PASSWORD = "demo_user", "demo123"
LOG_PATH = os.path.join(seed.DATA_DIR, "request_log.jsonl")

app = Flask(__name__)
app.secret_key = "mail-mock-env-secret"
app.config["SESSION_COOKIE_NAME"] = "mail_session"

lock = threading.RLock()
state = {"faults": STARTUP_FAULTS, "flags": faults.parse(STARTUP_FAULTS),
         "error500_fired": [], "auth_page_loads": 0, "fired": {}}

OPEN_PATHS = {"/login", "/health", "/reset", "/banner/dismiss"}
HTML_PAGE_ENDPOINTS = {"inbox", "email_view", "attachment_preview"}


def db():
    conn = sqlite3.connect(seed.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_seeded():
    if not os.path.exists(seed.DB_PATH) or not os.path.isdir(seed.ATTACH_DIR) or not os.listdir(seed.ATTACH_DIR):
        os.makedirs(seed.DATA_DIR, exist_ok=True)
        seed.seed()


def is_admin_path(path):
    return path.startswith("/admin/")


def require_admin():
    if request.headers.get("X-Admin-Token") != ADMIN_TOKEN:
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
        return redirect(url_for("login"))
    limit = faults.int_flag(flags, "session_expires_after")
    if limit is not None and session.get("auth_page_loads", 0) >= limit:
        session.clear()
        g.fault = "session_expires_after"
        return redirect(url_for("login", expired=1))
    if request.endpoint in HTML_PAGE_ENDPOINTS:
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
    return render_template("error404.html"), 404


@app.route("/")
def index():
    return redirect(url_for("inbox"))


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
            return redirect(url_for("inbox"))
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
    return redirect(request.referrer or url_for("inbox"))


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


FOLDERS = {"inbox": "Inbox", "sent": "Sent", "archive": "Archive"}


@app.context_processor
def sidebar():
    if not session.get("user"):
        return {}
    counts = {r["folder"]: r["n"] for r in db().execute("SELECT folder, COUNT(*) n FROM emails GROUP BY folder")}
    return {"folder_counts": counts, "FOLDERS": FOLDERS}


def sorted_rows(rows):
    asc = request.args.get("sort") == "date_asc"
    return sorted(rows, key=lambda r: (r["date"], r["id"]), reverse=not asc)


def list_view(folder):
    q = request.args.get("q", "").strip()
    rows = [dict(r) for r in db().execute("SELECT * FROM emails WHERE folder=?", (folder,))]
    if q:
        ql = q.lower()
        rows = [r for r in rows if ql in r["sender_name"].lower() or ql in r["sender_email"].lower()
                or ql in r["subject"].lower() or (folder == "sent" and ql in r["recipient_name"].lower())]
    return render_template("inbox.html", emails=sorted_rows(rows), q=q, folder=folder,
                           endpoint="inbox" if folder == "inbox" else "folder_view")


@app.route("/inbox")
def inbox():
    return list_view("inbox")


@app.route("/folder/<name>")
def folder_view(name):
    if name not in FOLDERS:
        abort(404)
    return list_view(name)


def get_email(email_id):
    row = db().execute("SELECT * FROM emails WHERE id=?", (email_id,)).fetchone()
    if row is None:
        abort(404)
    return dict(row)


@app.route("/email/<int:email_id>")
def email_view(email_id):
    e = get_email(email_id)
    same = sorted([dict(r) for r in db().execute("SELECT id, date FROM emails WHERE folder=?", (e["folder"],))],
                  key=lambda r: (r["date"], r["id"]), reverse=True)
    pos = [r["id"] for r in same].index(e["id"])
    newer = same[pos - 1]["id"] if pos > 0 else None
    older = same[pos + 1]["id"] if pos + 1 < len(same) else None
    contact = e["recipient_email"] if e["folder"] == "sent" else e["sender_email"]
    return render_template("email.html", e=e, newer=newer, older=older, contact=contact,
                           folder_label=FOLDERS[e["folder"]])


def contact_rows():
    """Distinct external contacts: senders of received mail and recipients of sent mail."""
    contacts = {}
    for r in db().execute("SELECT * FROM emails"):
        if r["folder"] == "sent":
            name, addr = r["recipient_name"], r["recipient_email"]
        else:
            name, addr = r["sender_name"], r["sender_email"]
        c = contacts.setdefault(addr, {"name": name, "email": addr, "count": 0, "last": ""})
        c["count"] += 1
        c["last"] = max(c["last"], r["date"])
    return sorted(contacts.values(), key=lambda c: (c["name"].lower(), c["email"]))


@app.route("/contacts")
def contacts():
    return render_template("contacts.html", contacts=contact_rows())


@app.route("/contact/<path:addr>")
def contact_view(addr):
    rows = [dict(r) for r in db().execute(
        "SELECT * FROM emails WHERE sender_email=? OR recipient_email=?", (addr, addr))
        if r["folder"] != "sent" and r["sender_email"] == addr or r["folder"] == "sent" and r["recipient_email"] == addr]
    if not rows:
        abort(404)
    name = rows[0]["recipient_name"] if rows[0]["folder"] == "sent" else rows[0]["sender_name"]
    return render_template("contact.html", name=name, addr=addr, emails=sorted_rows(rows))


@app.route("/help")
def help_page():
    return render_template("help.html")


def attachment_or_404(email_id):
    e = get_email(email_id)
    if not e["attachment_filename"]:
        abort(404)
    return e


@app.route("/email/<int:email_id>/attachment.pdf")
def attachment_pdf(email_id):
    e = attachment_or_404(email_id)
    flags = state["flags"]
    path = os.path.join(seed.ATTACH_DIR, e["attachment_filename"])
    if "pdf_missing_once" in flags and faults.bump(state, lock, "pdf_missing_once"):
        g.fault = "pdf_missing_once"
        abort(404)
    if "pdf_corrupt_once" in flags and faults.bump(state, lock, "pdf_corrupt_once"):
        g.fault = "pdf_corrupt_once"
        with open(path, "rb") as f:
            data = f.read()
        resp = Response(data[: max(64, len(data) // 3)], mimetype="application/pdf")
        resp.headers["Content-Disposition"] = f'attachment; filename="{e["attachment_filename"]}"'
        return resp
    resp = send_file(path, mimetype="application/pdf")
    resp.headers["Content-Disposition"] = f'attachment; filename="{e["attachment_filename"]}"'
    return resp


@app.route("/email/<int:email_id>/attachment")
def attachment_preview(email_id):
    e = attachment_or_404(email_id)
    return render_template("attachment.html", e=e, lines=seed.document_lines(e["attachment_filename"]))


@app.route("/health")
def health():
    return jsonify(status="ok", app="mail", today=APP_TODAY)


@app.route("/reset", methods=["POST"])
def reset():
    with lock:
        seed.seed()
        state["flags"] = faults.parse(STARTUP_FAULTS)
        state["faults"] = STARTUP_FAULTS
        state["error500_fired"] = []
        state["auth_page_loads"] = 0
        state["fired"] = {}
    return jsonify(status="reset", app="mail")


@app.route("/admin/state")
def admin_state():
    require_admin()
    emails = [{k: r[k] for k in ("id", "sender_name", "sender_email", "subject", "date", "attachment_filename", "folder")}
              for r in db().execute("SELECT * FROM emails ORDER BY id")]
    return jsonify(app="mail", today=APP_TODAY, emails=emails, faults=state["faults"],
                   counters={"auth_page_loads": state["auth_page_loads"],
                             "error500_fired": list(state["error500_fired"]),
                             "fired": dict(state["fired"])})


@app.route("/admin/faults", methods=["POST"])
def admin_faults():
    require_admin()
    body = request.get_json(silent=True) or {}
    text = body.get("faults", "")
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
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5001)), debug=False, threaded=True)
