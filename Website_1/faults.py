"""Fault-flag parsing and helpers.

Flags come from the FAULTS env var (comma separated, `name` or `name=value`) or from
POST /admin/faults. Always POST /reset before each scenario, then set the faults: /reset clears
runtime faults and every "fired once" counter.
"""
import socket
import sys
import time

from flask import Response

# Flags shared by both mock apps.
COMMON = {
    "slow_ms",                # slow_ms=N               every response delayed N ms
    "error500_once",          # error500_once=/path     first hit returns HTTP 500
    "session_expires_after",  # session_expires_after=N logged out after N page loads
    "slow_path",              # slow_path=/path:MS      only that path is slow
    "error503_once",          # error503_once=/path     first hit returns HTTP 503 + Retry-After
    "fail_first",             # fail_first=/path:K      first K hits return HTTP 500
    "drop_connection_once",   # drop_connection_once=/path  connection closed with no response
    "blank_page_once",        # blank_page_once=/path   HTTP 200 but an empty page
    "login_flaky_once",       # first correct login fails with a transient error
    "popup_banner",           # modal maintenance banner blocks the page until dismissed
}
KNOWN = COMMON | {
    "pdf_corrupt_once",       # first PDF download is truncated (cannot be opened)
    "pdf_missing_once",       # first PDF download returns HTTP 404
}
_warned = set()

ERROR_HTML = ('<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Error</title></head>'
              '<body><h1 data-testid="error-page">{}</h1></body></html>')
BLANK_HTML = '<!doctype html><html lang="en"><head><meta charset="utf-8"><title></title></head><body></body></html>'


def parse(flag_string):
    """Parse 'name,name=value' into a dict. Unknown names are ignored (warned once on stderr)."""
    flags = {}
    for part in (flag_string or "").split(","):
        part = part.strip()
        if not part:
            continue
        name, _, value = part.partition("=")
        name = name.strip()
        if name not in KNOWN:
            if name not in _warned:
                _warned.add(name)
                print(f"[faults] ignoring unknown flag: {name}", file=sys.stderr)
            continue
        flags[name] = value.strip()
    return flags


def int_flag(flags, name):
    """Return the integer value of a flag, or None if absent/invalid."""
    try:
        return int(flags[name])
    except (KeyError, ValueError):
        return None


def path_value(flags, name):
    """'/inbox:3000' -> ('/inbox', '3000'); '/inbox' -> ('/inbox', ''); absent -> None."""
    raw = flags.get(name)
    if raw is None:
        return None
    path, sep, rest = raw.rpartition(":")
    if not sep:
        return raw, ""
    return path, rest


def bump(state, lock, key, limit=1):
    """Count one occurrence of fault `key`. True while it is still within `limit` (the fault should fire)."""
    with lock:
        fired = state.setdefault("fired", {})
        n = fired.get(key, 0)
        if n >= limit:
            return False
        fired[key] = n + 1
        return True


def request_faults(state, lock, req):
    """Path-targeted faults. Returns (response_or_None, fault_name_or_None).

    A response means "return this instead of running the view". A name with no response means the
    fault only delayed the request (slow_path).
    """
    flags = state["flags"]
    path = req.path
    name = None

    pv = path_value(flags, "slow_path")
    if pv and pv[0] == path and pv[1].isdigit() and int(pv[1]) > 0:
        time.sleep(int(pv[1]) / 1000.0)
        name = "slow_path"

    if flags.get("drop_connection_once") == path and bump(state, lock, "drop_connection_once"):
        sock = req.environ.get("werkzeug.socket")
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)  # peer sees a reset / empty response
            except OSError:
                pass
            return Response("", status=200), "drop_connection_once"
        return Response(ERROR_HTML.format("Bad Gateway"), 502, mimetype="text/html"), "drop_connection_once"

    if flags.get("blank_page_once") == path and bump(state, lock, "blank_page_once"):
        return Response(BLANK_HTML, 200, mimetype="text/html"), "blank_page_once"

    if flags.get("error503_once") == path and bump(state, lock, "error503_once"):
        resp = Response(ERROR_HTML.format("Service Unavailable. Please retry shortly."), 503, mimetype="text/html")
        resp.headers["Retry-After"] = "1"
        return resp, "error503_once"

    pv = path_value(flags, "fail_first")
    if pv and pv[0] == path and pv[1].isdigit() and bump(state, lock, "fail_first", limit=int(pv[1])):
        return Response(ERROR_HTML.format("Internal Server Error"), 500, mimetype="text/html"), "fail_first"

    return None, name
