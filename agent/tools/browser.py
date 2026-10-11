"""Browser tools (Playwright, DOM-based). One context, one tab per app.

WHY THIS FILE EXISTS
--------------------
The two mock websites (Website_1 = "mail", Website_2 = "finance") are only usable through
a browser. These tools are the agent's "hands and eyes" on them:
  eyes  -> browser_snapshot   (what is on the page right now?)
  hands -> browser_open / click / fill / select / download / login

KEY TERMS
---------
* Playwright : a library that remote-controls a real Chromium browser from Python.
* DOM-based  : we read/act on the page's HTML elements (buttons, inputs) instead of
               looking at screenshots. Cheaper, faster, and far more reliable.
* data-testid: an HTML attribute (e.g. data-testid="banner-dismiss") that the mock sites
               add on purpose, so elements have stable names even when labels/layout change
               (the `rename_label` / `move_submit_button` faults test exactly this).
* context    : one browser "profile" (cookies/session shared by all tabs).
* tab per app: one page for mail, one for finance, so switching apps never loses state.

Status: all tools are implemented. The TODO comments stay, with a short DONE note under each.
"""
import hashlib
import os
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

from playwright.sync_api import sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from ..config import Config
from .base import ToolResult, tool

MAX_ELEMENTS = 40        # keeps a snapshot short (TOOLS.md: under ~3,000 characters)
TEXT_CHARS = 500
PAUSE_AFTER_ERROR = 0.5  # Chromium needs a moment after a dropped connection


# TODO: BrowserSession class holding playwright, context, pages{"mail","finance"}, allowed origins.
# Why a class: all tools need to share ONE browser instance (otherwise each call would launch a
# new browser and lose the login). "allowed origins" = the only two base URLs we may visit;
# policy.py relies on this so the agent can never wander to an arbitrary website.
#
# DONE: the browser only starts on the first tool call. get_session() below always gives back the
# same object, so every tool (and finance_api.py) shares one login.
class BrowserSession:
    def __init__(self, cfg):
        self.cfg = cfg
        self.base_urls = {"mail": cfg.mail_url.rstrip("/"), "finance": cfg.finance_url.rstrip("/")}
        self.download_dir = Path(cfg.run_dir) / "downloads"
        self.pages = {}
        # What the server last answered for each tab. A Playwright page doesn't say if it was a 500.
        self.last_status = {"mail": None, "finance": None}
        self.last_retry_after = {"mail": None, "finance": None}
        self.playwright = None
        self.chromium = None
        self.context = None

    def start(self):
        if self.context is not None:
            return
        self.playwright = sync_playwright().start()
        headless = os.environ.get("HEADLESS", "1") != "0"   # HEADLESS=0 shows the browser window
        self.chromium = self.playwright.chromium.launch(headless=headless)
        self.context = self.chromium.new_context()
        self.context.set_default_timeout(self.cfg.step_timeout_s * 1000)

        self.pages["mail"] = self.context.new_page()
        self.pages["finance"] = self.context.new_page()
        self.pages["mail"].on("response", self.mail_response)
        self.pages["finance"].on("response", self.finance_response)

    def mail_response(self, response):
        self.save_status("mail", response)

    def finance_response(self, response):
        self.save_status("finance", response)

    def save_status(self, app, response):
        if not response.request.is_navigation_request():
            return    # images, css and so on
        if 300 <= response.status < 400:
            return    # a redirect is not the final answer. If the connection then drops, no status is left
        self.last_status[app] = response.status
        self.last_retry_after[app] = response.headers.get("retry-after")

    def forget_status(self, app):
        self.last_status[app] = None
        self.last_retry_after[app] = None

    def close(self):
        try:
            if self.chromium is not None:
                self.chromium.close()
            if self.playwright is not None:
                self.playwright.stop()
        except Exception:
            pass    # we are shutting down anyway
        self.playwright = None
        self.chromium = None
        self.context = None
        self.pages = {}


the_session = None


def get_session():
    global the_session
    if the_session is None:
        # created here and not at import, so values from .env are already loaded
        the_session = BrowserSession(Config.from_env())
    return the_session


def close_session():
    global the_session
    if the_session is not None:
        the_session.close()
    the_session = None


def set_download_dir(path):
    get_session().download_dir = Path(path)


# --- helpers ---------------------------------------------------------------------------

def prepare(app):
    """Every tool starts with this. Returns an error result, or None when the browser is ready."""
    if app not in ("mail", "finance"):
        return ToolResult.fail("ERR_POLICY", f"unknown app '{app}', use mail or finance")
    try:
        get_session().start()
    except Exception as exc:
        return ToolResult.fail("ERR_NAV_FAILED", f"browser could not start: {exc}")
    return None


def first_line(exc):
    # Playwright error messages are long, the first line is enough
    return str(exc).split("\n")[0][:200]


def http_error(status, retry_after=None, what="request"):
    """Turns an HTTP status into one of our error codes. None means the status is fine."""
    if status is None or status < 400:
        return None
    if status == 404:
        return ToolResult.fail("ERR_HTTP_404", f"{what} returned 404")
    if status == 503:
        message = f"{what} returned 503"
        if retry_after:
            message = message + f", Retry-After: {retry_after}"
        return ToolResult.fail("ERR_HTTP_503", message)
    if status >= 500:
        return ToolResult.fail("ERR_HTTP_5XX", f"{what} returned {status}")
    return ToolResult.fail("ERR_NAV_FAILED", f"{what} returned {status}")


def page_is_blank(page):
    try:
        has_testids = page.locator("[data-testid]").count() > 0
        has_text = page.inner_text("body").strip() != ""
    except Exception:
        return True
    return not has_testids and not has_text


def banner_is_visible(page):
    try:
        return page.get_by_test_id("maintenance-banner").is_visible()
    except Exception:
        return False


def check_page(app, page, expect_login=False):
    """After a page load: returns an error result if something is wrong, else None."""
    session = get_session()
    status = session.last_status[app]

    error = http_error(status, session.last_retry_after[app], f"{app} page")
    if error is not None:
        return error

    if page.url.startswith("chrome-error"):
        time.sleep(PAUSE_AFTER_ERROR)
        return ToolResult.fail("ERR_NAV_FAILED", "connection dropped, no response")

    # sent to the login page without asking for it = the session ended
    if not expect_login and urlparse(page.url).path == "/login":
        return ToolResult.fail("ERR_SESSION_EXPIRED", "redirected to the login page, log in again")

    if page_is_blank(page):
        # no status at all means the connection dropped (Chromium keeps the old url and an empty
        # document). An empty page that got a 200 is a real blank page.
        if status is None:
            time.sleep(PAUSE_AFTER_ERROR)
            return ToolResult.fail("ERR_NAV_FAILED", "connection dropped, no response")
        return ToolResult.fail("ERR_BLANK_PAGE", "page came back empty, reload it")

    return None


def find_element(page, target):
    """Testid first, it is the stable one. Then visible text or label. None if nothing matches."""
    ways_to_look = [
        page.get_by_test_id(target),
        page.get_by_role("button", name=target),
        page.get_by_role("link", name=target),
        page.get_by_label(target),
        page.get_by_text(target),
    ]
    for way in ways_to_look:
        if way.count() > 0:
            return way.first
    return None


def no_element_error(target):
    return ToolResult.fail("ERR_NO_ELEMENT", f"nothing on the page matches '{target}', take a snapshot")


def action_error(exc):
    if isinstance(exc, PlaywrightTimeout):
        return ToolResult.fail("ERR_TIMEOUT", "timed out. If this was a submit it may still have gone "
                                              "through: read it back before trying again")
    return ToolResult.fail("ERR_VALIDATION", first_line(exc))


def read_flashes(page):
    try:
        texts = page.get_by_test_id("flash").all_inner_texts()
    except Exception:
        return []
    return [text.strip() for text in texts]


# --- tools -----------------------------------------------------------------------------

@tool("browser_open", "Open a path in an app", {"app": "mail|finance", "path": "str"})
def browser_open(app, path="/"):
    # `app` picks which site (and tab); `path` is appended to that site's base URL.
    # The agent passes only a path, never a full URL -> it cannot leave the two allowed sites.
    # TODO: goto with timeout; map status/exception -> ERR_HTTP_5XX / ERR_NAV_FAILED / ERR_TIMEOUT;
    #       wait ~0.5s before retry after a navigation error (Chromium quirk).
    #   - timeout        : so a slow page (slow_ms / slow_path fault) can't hang the agent forever.
    #   - status mapping : turns raw HTTP 500/503 or a dropped connection into our stable error
    #                      codes, so the loop can decide "retry with backoff" without the LLM.
    #   - the 0.5s wait : after drop_connection_once, an immediate second goto() is reported by
    #                      Chromium as "interrupted by another navigation"; waiting avoids that.
    #
    # DONE: goto() uses the step timeout from the config. check_page() maps 404/503/5xx, a redirect
    # to /login and empty pages. On a navigation error we sleep 0.5s right away, so the retry is fine.
    error = prepare(app)
    if error is not None:
        return error
    session = get_session()
    page = session.pages[app]

    if path is None or path == "":
        path = "/"
    # "http://evil.example" and "//evil.example" are not paths on our site
    if not path.startswith("/") or path.startswith("//"):
        return ToolResult.fail("ERR_POLICY", "give a path such as /inbox, not a full URL")

    session.forget_status(app)
    try:
        page.goto(session.base_urls[app] + path)
    except PlaywrightTimeout:
        return ToolResult.fail("ERR_TIMEOUT", f"{path} took longer than {session.cfg.step_timeout_s}s")
    except Exception as exc:
        time.sleep(PAUSE_AFTER_ERROR)
        return ToolResult.fail("ERR_NAV_FAILED", first_line(exc))

    problem = check_page(app, page, expect_login=path.startswith("/login"))
    if problem is not None:
        return problem

    status = session.last_status[app]
    data = {"status": status, "url": page.url, "title": page.title()}
    return ToolResult(True, data, evidence=f"GET {page.url} -> {status}")


def read_elements(page):
    """The clickable things on the page, in page order. Returns (elements, more_were_left_out)."""
    role_of_tag = {"a": "link", "button": "button", "input": "textbox", "select": "select", "textarea": "textbox"}

    elements = []
    for element in page.locator("a[href], button, input, select, textarea").all():
        # page order matters: on the inbox the search box comes before 30 email links
        if len(elements) >= MAX_ELEMENTS:
            return elements, True
        if not element.is_visible():
            continue
        input_type = element.get_attribute("type")
        if input_type == "hidden":
            continue

        tag = element.evaluate("e => e.tagName.toLowerCase()")   # the one bit of JavaScript we need
        role = role_of_tag[tag]

        item = {"role": role}
        item["testid"] = element.get_attribute("data-testid") or ""
        if role in ("link", "button"):
            item["text"] = element.inner_text().strip()[:40]
        if role == "link":
            item["href"] = element.get_attribute("href") or ""
        if role in ("textbox", "select"):
            # boxes have no text of their own, the placeholder or field name says what they are for
            item["label"] = (element.get_attribute("aria-label")
                             or element.get_attribute("placeholder")
                             or element.get_attribute("name") or "")
        if role == "textbox" and input_type != "password":
            item["value"] = element.input_value()[:40]
        if role == "select":
            try:
                item["value"] = element.locator("option:checked").first.inner_text().strip()[:40]
            except Exception:
                item["value"] = ""
        if not element.is_enabled():
            item["enabled"] = False

        # nothing to tell the model about this one
        if not (item["testid"] or item.get("text") or item.get("label") or item.get("href")):
            continue

        # drop empty values to keep the snapshot short
        short_item = {}
        for key, value in item.items():
            if value != "":
                short_item[key] = value
        elements.append(short_item)
    return elements, False


@tool("browser_snapshot", "Compact view of the page: text + interactive elements", {"app": "mail|finance"})
def browser_snapshot(app):
    # The agent's MAIN observation. Instead of dumping raw HTML (huge, noisy, expensive in
    # tokens) we return a short summary the LLM can reason about.
    # TODO: url, title, trimmed text, elements[{testid,role,label,text,enabled}], banner flag, flashes;
    #       ERR_BLANK_PAGE if no landmarks.
    #   - elements       : the clickable/typeable things, each named by testid/label so the agent
    #                      knows what `target` values it may pass to click/fill/select.
    #   - banner flag    : tells the agent the maintenance popup is covering the page (popup_banner
    #                      fault) so it dismisses it instead of treating it as task content.
    #   - flashes        : the green/red messages ("Invoice saved", "Temporary system error").
    #   - ERR_BLANK_PAGE : if none of the expected landmarks exist, the page came back empty
    #                      (blank_page_once fault) -> signal "reload", don't invent content.
    #
    # DONE: returns all of the above plus the per-field form errors. Text and element count are cut
    # (TEXT_CHARS, MAX_ELEMENTS). Empty keys are left out and `enabled` only shows up when False.
    error = prepare(app)
    if error is not None:
        return error
    page = get_session().pages[app]

    if page.url == "about:blank":
        return ToolResult.fail("ERR_NAV_FAILED", f"nothing is open in {app} yet, use browser_open")
    if page_is_blank(page):
        return ToolResult.fail("ERR_BLANK_PAGE", "page came back empty, reload it")

    elements, more = read_elements(page)
    text = " ".join(page.inner_text("body").split())   # squeeze all whitespace into single spaces

    # form errors have testids like error-invoice_no (error-page is the HTTP 500 page, not a field)
    form_errors = {}
    for element in page.locator('[data-testid^="error-"]').all():
        testid = element.get_attribute("data-testid")
        if testid == "error-page" or not element.is_visible():
            continue
        form_errors[testid.replace("error-", "", 1)] = element.inner_text().strip()[:120]

    data = {
        "url": page.url,
        "title": page.title(),
        "text": text[:TEXT_CHARS],
        "elements": elements,
        "flashes": read_flashes(page),
        "errors": form_errors,
        "banner": banner_is_visible(page),
    }
    if more:
        data["more_elements"] = True
    return ToolResult(True, data, evidence=f"snapshot of {page.url}")


@tool("browser_click", "Click an element by testid or visible text", {"app": "mail|finance", "target": "str"})
def browser_click(app, target):
    # `target` can be a testid (preferred, stable) or visible text (fallback).
    # TODO: ERR_INTERCEPTED with blocker testid when an overlay blocks the click.
    #   Playwright refuses to click if another element (the modal banner) sits on top and
    #   raises "intercepts pointer events". We convert that into ERR_INTERCEPTED and report
    #   WHICH element blocks (e.g. "maintenance-banner") so the agent can dismiss it first.
    #
    # DONE: if the banner is up we return ERR_INTERCEPTED right away (no 10s wait). If Playwright
    # still reports a blocker, we read its testid out of the message. After the click we wait for the
    # page and run check_page(), so a save that answers 500 shows up as ERR_HTTP_5XX.
    error = prepare(app)
    if error is not None:
        return error
    session = get_session()
    page = session.pages[app]

    element = find_element(page, target)
    if element is None:
        return no_element_error(target)

    # while the popup is up, only its own button can be clicked
    if banner_is_visible(page) and element.get_attribute("data-testid") != "banner-dismiss":
        return ToolResult.fail("ERR_INTERCEPTED", "blocked by maintenance-banner, click banner-dismiss first")

    path_before = urlparse(page.url).path
    session.forget_status(app)
    try:
        element.click()
    except Exception as exc:
        message = str(exc)
        if "intercepts pointer events" in message:
            blocker = "another element"
            if 'data-testid="' in message:
                blocker = message.split('data-testid="')[1].split('"')[0]
            return ToolResult.fail("ERR_INTERCEPTED", f"blocked by {blocker}")
        return action_error(exc)

    try:
        page.wait_for_load_state("load")
    except Exception as exc:
        return action_error(exc)

    problem = check_page(app, page, expect_login=(path_before == "/login"))
    if problem is not None:
        # keep what the page said. After a submit this keeps a false "Invoice saved" visible
        flashes = read_flashes(page)
        if len(flashes) > 0:
            problem.error_message = problem.error_message + " (page said: " + " | ".join(flashes) + ")"
        return problem

    data = {"clicked": target, "url": page.url, "status": session.last_status[app], "flashes": read_flashes(page)}
    return ToolResult(True, data, evidence=f"clicked {target}, now at {page.url}")


@tool("browser_fill", "Type into a field", {"app": "mail|finance", "target": "str", "value": "str"})
def browser_fill(app, target, value):
    # Types `value` into the input named by `target`. Per docs/TOOLS.md it should return the
    # value actually present in the field afterwards, so the agent/checker can confirm it was
    # typed correctly (no silent truncation or wrong-field typing).
    #
    # DONE: fills the box, then reads it back. If the box holds something else we return an error.
    # Passwords are never echoed back.
    error = prepare(app)
    if error is not None:
        return error
    page = get_session().pages[app]

    element = find_element(page, target)
    if element is None:
        return no_element_error(target)

    try:
        element.fill(str(value))
        actual = element.input_value()
        is_password = element.get_attribute("type") == "password"
    except Exception as exc:
        return action_error(exc)

    if actual != str(value):
        return ToolResult.fail("ERR_VALIDATION", f"typed into {target} but it holds '{actual}'")

    shown = actual
    if is_password:
        shown = "***"
    return ToolResult(True, {"target": target, "value": shown}, evidence=f"{target} = {shown}")


@tool("browser_select", "Choose a dropdown option", {"app": "mail|finance", "target": "str", "option": "str"})
def browser_select(app, target, option):
    # For <select> dropdowns (e.g. the Vendor dropdown on the new-invoice form).
    # `option` may be the visible label or the underlying value. Returns the selected option.
    #
    # DONE: matches the label first, then the value. If neither exists the error lists the options
    # that do exist, so the model doesn't have to guess.
    error = prepare(app)
    if error is not None:
        return error
    page = get_session().pages[app]

    element = find_element(page, target)
    if element is None:
        return no_element_error(target)

    choices = []    # (value, label) pairs
    for choice in element.locator("option").all():
        value = choice.get_attribute("value") or ""
        label = choice.inner_text().strip()
        choices.append((value, label))

    match = None
    for value, label in choices:
        if label == option:
            match = (value, label)
            break
    if match is None:
        for value, label in choices:
            if value == option:
                match = (value, label)
                break

    if match is None:
        available = []
        for value, label in choices[:20]:
            available.append(f"{value or '-'}={label}")
        return ToolResult.fail("ERR_VALIDATION", f"no option '{option}' in {target}. Options: " + ", ".join(available))

    try:
        element.select_option(value=match[0])
    except Exception as exc:
        return action_error(exc)

    data = {"target": target, "selected": match[1], "value": match[0]}
    return ToolResult(True, data, evidence=f"{target} = {match[1]} ({match[0]})")


@tool("browser_download", "Download an attachment to the run folder", {"app": "mail|finance", "target": "str"})
def browser_download(app, target):
    # Used to fetch the invoice PDF attached to an email.
    # TODO: save to runs/<id>/downloads, return path/size/sha256.
    #   - runs/<id>/downloads : each run gets its own folder so runs never overwrite each other
    #                           and the audit trail keeps the exact file the agent used.
    #   - size + sha256       : a file fingerprint; proves which file was processed and lets us
    #                           spot a truncated download (pdf_corrupt_once) or compare re-downloads.
    #   - errors              : a 404 -> ERR_HTTP_404 (pdf_missing_once); other failures -> ERR_DOWNLOAD.
    #
    # DONE: instead of waiting for a browser download event we read the link's href and fetch it with
    # the browser's request client (same cookies). That gives us the HTTP status directly. An existing
    # file name gets -2, -3 added, so a re-download after a corrupt file keeps both.
    error = prepare(app)
    if error is not None:
        return error
    session = get_session()
    page = session.pages[app]

    element = find_element(page, target)
    if element is None:
        return no_element_error(target)
    link = element.get_attribute("href")
    if not link:
        return ToolResult.fail("ERR_DOWNLOAD", f"{target} is not a link to a file")

    url = urljoin(page.url, link)
    if not url.startswith(session.base_urls[app] + "/"):
        return ToolResult.fail("ERR_POLICY", "download link points outside the allowed site")

    try:
        response = session.context.request.get(url)
        body = response.body()
    except PlaywrightTimeout:
        return ToolResult.fail("ERR_TIMEOUT", "download timed out")
    except Exception as exc:
        time.sleep(PAUSE_AFTER_ERROR)
        return ToolResult.fail("ERR_NAV_FAILED", first_line(exc))

    if urlparse(response.url).path == "/login":
        return ToolResult.fail("ERR_SESSION_EXPIRED", "download redirected to the login page")
    problem = http_error(response.status, response.headers.get("retry-after"), "download")
    if problem is not None:
        return problem
    content_type = response.headers.get("content-type", "").lower()
    if "pdf" not in content_type and not body.startswith(b"%PDF"):
        return ToolResult.fail("ERR_DOWNLOAD", "the response is not a PDF")

    # The url ends in attachment.pdf for every email, the real name is in this header:
    # Content-Disposition: attachment; filename="INV-X-1042.pdf"
    name = Path(urlparse(url).path).name
    header = response.headers.get("content-disposition", "")
    if 'filename="' in header:
        name = Path(header.split('filename="')[1].split('"')[0]).name
    if name == "":
        name = "download.pdf"

    session.download_dir.mkdir(parents=True, exist_ok=True)
    destination = session.download_dir / name
    counter = 2
    while destination.exists():
        destination = session.download_dir / f"{Path(name).stem}-{counter}{Path(name).suffix}"
        counter = counter + 1
    destination.write_bytes(body)

    fingerprint = hashlib.sha256(body).hexdigest()
    data = {"path": str(destination), "size": len(body), "sha256": fingerprint}
    return ToolResult(True, data, evidence=f"{destination.name}, {len(body)} bytes, sha256 {fingerprint[:12]}")


@tool("browser_login", "Log in with sandbox credentials from config", {"app": "mail|finance"})
def browser_login(app):
    # Notice the tool takes NO username/password argument. Credentials come from config.py,
    # never from the model or the prompt (design rule 4) - the LLM can't leak or change them.
    # TODO: handle /login?expired=1 and the transient "temporarily unavailable" error -> ERR_LOGIN_TRANSIENT.
    #   - /login?expired=1 : session_expires_after fault logged us out mid-task; log in again and
    #                        resume, don't restart the whole task.
    #   - transient error  : login_flaky_once shows "Sign-in service temporarily unavailable" even
    #                        with correct credentials. It's NOT a wrong password, so retry rather
    #                        than give up (ERR_LOGIN_TRANSIENT tells the loop it is retryable).
    #
    # DONE: always opens a fresh /login (this also drops "?expired=1"), types the credentials from the
    # config and signs in. "temporarily unavailable" -> ERR_LOGIN_TRANSIENT, any other failure to
    # leave /login -> ERR_LOGIN_FAILED (wrong password, retrying won't help).
    error = prepare(app)
    if error is not None:
        return error
    session = get_session()
    page = session.pages[app]

    if app == "mail":
        username = session.cfg.mail_user
        password = session.cfg.mail_password
    else:
        username = session.cfg.finance_user
        password = session.cfg.finance_password

    opened = browser_open(app, "/login")
    if not opened.ok:
        return opened
    if banner_is_visible(page):
        return ToolResult.fail("ERR_INTERCEPTED", "blocked by maintenance-banner, click banner-dismiss first")

    session.forget_status(app)
    try:
        page.get_by_test_id("username").fill(username)
        page.get_by_test_id("password").fill(password)
        page.get_by_test_id("login-submit").click()
        page.wait_for_load_state("load")
    except Exception as exc:
        return action_error(exc)

    # after a good login the site redirects to another page, which can be broken too
    problem = check_page(app, page, expect_login=True)
    if problem is not None:
        return problem

    if urlparse(page.url).path == "/login":
        message = " ".join(read_flashes(page))
        if "temporarily unavailable" in message.lower():
            return ToolResult.fail("ERR_LOGIN_TRANSIENT", message)
        if message == "":
            message = "still on the login page"
        return ToolResult.fail("ERR_LOGIN_FAILED", message)

    return ToolResult(True, {"logged_in": True, "app": app, "url": page.url}, evidence=f"logged in to {app}")
