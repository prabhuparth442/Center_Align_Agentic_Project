"""DRAFT of agent/tools/browser.py with every tool written out in plain, readable Python.
Read it top to bottom, then copy what you understand into browser.py. Delete this file afterwards.

Nothing imports this file. It is only here to learn from.
(If you do import it, its tools replace the ones in browser.py, because they use the same names.)

=====================================================================================================
WHAT THIS FILE IS FOR
=====================================================================================================
The agent (an LLM) cannot click anything by itself. It can only ASK for a tool, for example
    {"tool": "browser_click", "args": {"app": "mail", "target": "search-submit"}}
and our code then does the clicking. This file holds the seven tools that control a real web browser:

    browser_open      go to a page                          (the agent's legs)
    browser_snapshot  describe what is on the page          (the agent's EYES, the most important one)
    browser_click     click a button or a link
    browser_fill      type text into a box
    browser_select    choose an option in a dropdown
    browser_download  save a file (the invoice PDF) to disk
    browser_login     sign in with the sandbox username and password from the config

=====================================================================================================
THE FOUR IDEAS YOU NEED
=====================================================================================================
1. PLAYWRIGHT is a library that remote-controls a real Chromium browser from Python. We use its
   "sync" API, which means each call waits until it is finished (no async / await to learn).

2. data-testid. The two mock websites put a fixed name on every button and box, for example
   data-testid="submit-invoice". Labels and layouts may change (that is one of the faults we test), but
   these names do not. So the agent clicks "submit-invoice", not "the green button at the bottom".

3. ONE SESSION, TWO TABS. All tools share one browser with one tab for "mail" and one for "finance".
   They must share it, otherwise every tool call would open a new browser and forget the login.
   The class BrowserSession below holds it. get_session() always returns the same one.

4. STABLE ERROR CODES. Every tool returns a ToolResult (see base.py). When something goes wrong the
   tool does NOT raise an error and does NOT write a sentence for the model to interpret. It returns
   ToolResult.fail("ERR_SOMETHING", "short explanation"). The loop and checker.py then make decisions
   from the code alone, for example "ERR_HTTP_5XX means wait and try again". The codes used here:

       ERR_HTTP_5XX / ERR_HTTP_503   the server answered with an error           -> retry later
       ERR_HTTP_404                  the page or file does not exist
       ERR_NAV_FAILED                connection dropped, browser could not start -> retry
       ERR_TIMEOUT                   took longer than the step timeout           -> wait, then read back
       ERR_BLANK_PAGE                the page came back empty                    -> reload
       ERR_SESSION_EXPIRED           we were sent to the login page              -> log in again
       ERR_INTERCEPTED               a popup is covering the page                -> click banner-dismiss
       ERR_LOGIN_TRANSIENT           the login service had a temporary hiccup    -> try again
       ERR_LOGIN_FAILED              wrong username or password                  -> not fixable by retrying
       ERR_NO_ELEMENT                nothing on the page matches the target
       ERR_VALIDATION                the page refused the input (wrong text typed, option missing...)
       ERR_DOWNLOAD                  the thing we tried to download is not a PDF
       ERR_POLICY                    we refuse to do this (unknown app, address outside the two sites)

=====================================================================================================
HOW THE FILE IS ORGANISED
=====================================================================================================
    imports and settings
    BrowserSession class + get_session()      the one shared browser
    small helper functions                    used by several tools
    the seven tools                           @tool(...) functions, in the order listed above
"""
import hashlib
import os
import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

from ..config import Config
from .base import ToolResult, tool

# Playwright is imported inside try/except on purpose. If it is not installed, importing this file
# must still work (the rest of the agent and its tests do not need a browser). In that case we only
# fail later, with a clear message, at the moment somebody really tries to start the browser.
try:
    from playwright.sync_api import sync_playwright
    from playwright.sync_api import TimeoutError as PlaywrightTimeout
except ImportError:
    sync_playwright = None
    PlaywrightTimeout = TimeoutError   # a stand-in so that "except PlaywrightTimeout" still works

MAX_ELEMENTS = 40            # a snapshot lists at most this many clickable things (keeps it small)
TEXT_CHARS = 500             # and at most this many characters of page text
RETRY_PAUSE_SECONDS = 0.5    # Chromium quirk: after a dropped connection, wait a moment before the next try


# =====================================================================================================
# THE SHARED BROWSER
# =====================================================================================================
class BrowserSession:
    """Owns the one Chromium browser, its two tabs, and a little memory of what the server last said.

    Why a class? Because several things belong together and must live as long as the run does:
    the browser, the tabs, the folder for downloads and the last HTTP status of each tab. A class lets
    all of them be stored in one place (self.something) and be shared by every tool.
    """

    def __init__(self, cfg):
        self.cfg = cfg
        # Base address of each site. rstrip("/") removes a trailing slash so that
        # base + "/inbox" never becomes "...5001//inbox".
        self.base_urls = {"mail": cfg.mail_url.rstrip("/"), "finance": cfg.finance_url.rstrip("/")}
        self.timeout_ms = cfg.step_timeout_s * 1000        # Playwright wants milliseconds
        self.download_dir = Path(cfg.run_dir) / "downloads"

        self.pages = {}                                    # "mail" -> its tab, "finance" -> its tab
        # What the server answered the last time each tab loaded a page. A Playwright page object
        # does not tell you whether the answer was 500, so we listen for it (see remember_response).
        self.last_status = {"mail": None, "finance": None}
        self.last_retry_after = {"mail": None, "finance": None}

        # Playwright objects. They stay None until start() is called.
        self.playwright = None
        self.browser = None
        self.context = None      # a "context" = one browser profile. Cookies live in it, so both tabs share the login.

    def start(self):
        """Open the browser the first time a tool needs it. Later calls do nothing."""
        if self.context is not None:
            return                                         # already running
        if sync_playwright is None:
            raise RuntimeError("Playwright is not installed. Run: pip install playwright && playwright install chromium")

        self.playwright = sync_playwright().start()
        # headless = no visible window. Set the environment variable HEADLESS=0 to watch it work.
        headless = os.environ.get("HEADLESS", "1") != "0"
        self.browser = self.playwright.chromium.launch(headless=headless)
        self.context = self.browser.new_context()
        self.context.set_default_timeout(self.timeout_ms)  # every wait inside Playwright now gives up after this long

        self.pages["mail"] = self.context.new_page()
        self.pages["finance"] = self.context.new_page()
        # "response" fires every time the browser receives an answer. We hook one function per tab.
        # (Two small functions are easier to read than one clever function that builds functions.)
        self.pages["mail"].on("response", self.on_mail_response)
        self.pages["finance"].on("response", self.on_finance_response)

    def on_mail_response(self, response):
        self.remember_response("mail", response)

    def on_finance_response(self, response):
        self.remember_response("finance", response)

    def remember_response(self, app, response):
        # Pictures, stylesheets and so on also produce responses. We only care about page loads.
        if not response.request.is_navigation_request():
            return
        # A redirect (status 300-399) is only a step on the way. Only the FINAL answer counts. This also
        # makes a dropped connection visible: if the connection dies after a redirect, no final answer
        # arrives and last_status stays None, which is how we tell "dropped" from "blank page".
        if 300 <= response.status < 400:
            return
        self.last_status[app] = response.status
        self.last_retry_after[app] = response.headers.get("retry-after")

    def forget_status(self, app):
        """Call this right before doing something that loads a page, so an old status cannot be mistaken for the new one."""
        self.last_status[app] = None
        self.last_retry_after[app] = None

    def close(self):
        try:
            if self.browser is not None:
                self.browser.close()
            if self.playwright is not None:
                self.playwright.stop()
        except Exception:
            pass     # closing is best effort. The program is ending anyway.
        self.context = None
        self.browser = None
        self.playwright = None
        self.pages = {}


# The single shared session. It starts as None and is created the first time somebody asks for it.
the_session = None


def get_session():
    """Return THE session. Browser tools and finance_api.py both call this, so they share cookies."""
    global the_session       # "global" lets this function change the module-level variable above
    if the_session is None:
        the_session = BrowserSession(Config.from_env())
    return the_session


def close_session():
    """The loop calls this at the end of a run so no Chromium process is left running."""
    global the_session
    if the_session is not None:
        the_session.close()
    the_session = None


def set_download_dir(path):
    """The loop calls this with runs/<id>/downloads, so every run keeps its own files."""
    get_session().download_dir = Path(path)


# =====================================================================================================
# SMALL HELPERS (used by several tools)
# =====================================================================================================
def first_line(error):
    """The first line of an exception message, cut short. Playwright's messages are very long."""
    lines = str(error).splitlines()
    if len(lines) == 0:
        return type(error).__name__
    return lines[0][:200]


def prepare(app):
    """Every tool starts with this. Returns an error result, or None when the browser is ready."""
    if app not in ("mail", "finance"):
        return ToolResult.fail("ERR_POLICY", f"unknown app '{app}', use mail or finance")
    try:
        get_session().start()
    except Exception as exc:
        return ToolResult.fail("ERR_NAV_FAILED", f"browser could not start: {exc}")
    return None


def http_error(status, retry_after=None, what="request"):
    """Turn an HTTP status number into one of our error codes. Returns None when the status is fine.
    finance_api.py uses this too, which is why it is a normal function and not hidden inside a tool."""
    if status is None or status < 400:
        return None
    if status == 404:
        return ToolResult.fail("ERR_HTTP_404", f"{what} returned 404")
    if status == 503:
        extra = ""
        if retry_after:
            extra = f", Retry-After: {retry_after}"      # the server says how long to wait
        return ToolResult.fail("ERR_HTTP_503", f"{what} returned 503{extra}")
    if status >= 500:
        return ToolResult.fail("ERR_HTTP_5XX", f"{what} returned {status}")
    return ToolResult.fail("ERR_NAV_FAILED", f"{what} returned {status}")


def page_is_blank(page):
    """True if the page has nothing we could use: no data-testid anywhere and no visible text."""
    try:
        has_testids = page.locator("[data-testid]").count() > 0
        has_text = page.inner_text("body").strip() != ""
    except Exception:
        return True
    return not has_testids and not has_text


def banner_is_visible(page):
    """The maintenance popup (the popup_banner fault) covers the page and blocks clicks."""
    try:
        return page.get_by_test_id("maintenance-banner").is_visible()
    except Exception:
        return False


def check_page(app, page, expect_login=False):
    """After a page load: did it go wrong? Returns an error result, or None if the page looks fine.
    The order of the checks matters, the first problem found wins."""
    session = get_session()
    status = session.last_status[app]

    # 1. The server answered with an error status (404, 500, 503...).
    error = http_error(status, session.last_retry_after[app], f"{app} page")
    if error is not None:
        return error

    # 2. Chromium shows its own "chrome-error" page when it could not connect at all.
    if page.url.startswith("chrome-error"):
        time.sleep(RETRY_PAUSE_SECONDS)
        return ToolResult.fail("ERR_NAV_FAILED", "connection dropped, no response")

    # 3. We were sent to the login page although we did not ask for it: the session ended.
    if not expect_login and urlparse(page.url).path == "/login":
        return ToolResult.fail("ERR_SESSION_EXPIRED", "redirected to the login page, log in again")

    # 4. An empty page. Two very different causes:
    #    - the server never sent a final answer (status is None)  -> the connection was dropped
    #    - the server answered 200 with nothing in it             -> a real blank page
    if page_is_blank(page):
        if status is None:
            time.sleep(RETRY_PAUSE_SECONDS)
            return ToolResult.fail("ERR_NAV_FAILED", "connection dropped, no response")
        return ToolResult.fail("ERR_BLANK_PAGE", "page came back empty, reload it")

    return None


def find_element(page, target):
    """Find the thing the agent named. Tries the stable name (data-testid) first, then falls back to
    visible words. Returns the element, or None if nothing matches."""
    ways_to_look = [
        page.get_by_test_id(target),                   # best: <button data-testid="submit-invoice">
        page.get_by_role("button", name=target),       # a button whose text contains the target
        page.get_by_role("link", name=target),         # a link whose text contains the target
        page.get_by_label(target),                     # an input whose <label> contains the target
        page.get_by_text(target),                      # any element that shows this text
    ]
    for way in ways_to_look:
        if way.count() > 0:
            return way.first                           # .first: if several match, take the first one
    return None


def no_element_error(target):
    return ToolResult.fail("ERR_NO_ELEMENT", f"nothing on the page matches '{target}', take a snapshot")


def action_error(error):
    """An action (click, fill...) raised an exception. Turn it into our error codes."""
    if isinstance(error, PlaywrightTimeout):
        return ToolResult.fail(
            "ERR_TIMEOUT",
            "timed out. If this was a submit it may still have gone through: read it back before trying again")
    return ToolResult.fail("ERR_VALIDATION", first_line(error))


def read_flashes(page):
    """The green / red messages the site shows after an action, e.g. 'Invoice INV-1 saved.'"""
    try:
        texts = page.get_by_test_id("flash").all_inner_texts()
    except Exception:
        return []
    return [text.strip() for text in texts]


# =====================================================================================================
# TOOL 1: browser_open
# =====================================================================================================
@tool("browser_open", "Open a path in an app", {"app": "mail|finance", "path": "str"})
def browser_open(app, path="/"):
    """Go to a page, e.g. browser_open("mail", "/inbox").

    The agent gives only a PATH, never a full web address. We glue the path to the site's base address
    ourselves. That is the safety rule: the agent cannot visit any website except these two.
    """
    error = prepare(app)
    if error is not None:
        return error
    session = get_session()
    page = session.pages[app]

    # "/inbox" is fine. "http://evil.example" and "//evil.example" are not paths on OUR site, so refuse.
    if path is None or path == "":
        path = "/"
    if not path.startswith("/") or path.startswith("//"):
        return ToolResult.fail("ERR_POLICY", "give a path such as /inbox, not a full URL")

    session.forget_status(app)
    try:
        page.goto(session.base_urls[app] + path)           # waits until the page has loaded or the timeout hits
    except PlaywrightTimeout:
        return ToolResult.fail("ERR_TIMEOUT", f"{path} took longer than {session.cfg.step_timeout_s}s")
    except Exception as exc:
        # Usually a dropped connection. Chromium reports an immediate second try as "interrupted", so
        # we pause here. By the time the agent retries, the pause is over.
        time.sleep(RETRY_PAUSE_SECONDS)
        return ToolResult.fail("ERR_NAV_FAILED", first_line(exc))

    # The page loaded, but maybe it is an error page, a login redirect or empty.
    problem = check_page(app, page, expect_login=path.startswith("/login"))
    if problem is not None:
        return problem

    status = session.last_status[app]
    data = {"status": status, "url": page.url, "title": page.title()}
    return ToolResult(True, data, evidence=f"GET {page.url} -> {status}")


# =====================================================================================================
# TOOL 2: browser_snapshot  (the agent's eyes)
# =====================================================================================================
def read_elements(page):
    """List the clickable / typeable things on the page, in a short form the model can use.

    Returns (list_of_elements, there_are_more). Each element looks like
        {"testid": "submit-invoice", "role": "button", "text": "Save Invoice"}
    and only has the keys that are not empty.

    The elements come back in the order they appear on the page. That matters: on the inbox the search box
    is at the top and 30 email links follow. If we listed all links first, the list would be full before we
    ever reached the search box.
    """
    # The tag name of an element tells us what kind of thing it is.
    role_of_tag = {"a": "link", "button": "button", "input": "textbox", "select": "select", "textarea": "textbox"}

    elements = []
    for element in page.locator("a[href], button, input, select, textarea").all():
        if len(elements) >= MAX_ELEMENTS:
            return elements, True                          # stop early: the list is long enough
        if not element.is_visible():
            continue                                       # hidden things cannot be clicked, skip them
        input_type = element.get_attribute("type")
        if input_type == "hidden":
            continue

        # evaluate() runs a tiny piece of JavaScript inside the page. Here it only asks for the tag name.
        tag = element.evaluate("e => e.tagName.toLowerCase()")
        role = role_of_tag[tag]

        item = {"role": role}
        item["testid"] = element.get_attribute("data-testid") or ""
        if role in ("link", "button"):
            item["text"] = element.inner_text().strip()[:40]
        if role == "link":
            item["href"] = element.get_attribute("href") or ""
        if role in ("textbox", "select"):
            # Boxes have no visible text of their own, so tell the model what the box is for: the hint
            # inside it (placeholder) or its field name.
            item["label"] = (element.get_attribute("aria-label")
                             or element.get_attribute("placeholder")
                             or element.get_attribute("name") or "")
        if role == "textbox" and input_type != "password":     # never read passwords
            item["value"] = element.input_value()[:40]
        if role == "select":
            try:
                item["value"] = element.locator("option:checked").first.inner_text().strip()[:40]
            except Exception:
                item["value"] = ""
        if not element.is_enabled():
            item["enabled"] = False                        # only mentioned when it is disabled

        # Skip things that tell the model nothing: no name, no text, no label and no link.
        if not (item["testid"] or item.get("text") or item.get("label") or item.get("href")):
            continue
        # Leave out empty values so the snapshot stays short.
        elements.append({key: value for key, value in item.items() if value != ""})
    return elements, False


def read_form_errors(page):
    """Per-field error messages of a form, as {"invoice_no": "Invoice number must be ..."}."""
    errors = {}
    for element in page.locator('[data-testid^="error-"]').all():   # ^= means "starts with"
        testid = element.get_attribute("data-testid")
        if testid == "error-page":                         # that one is the HTTP 500 page, not a field
            continue
        if element.is_visible():
            errors[testid[len("error-"):]] = element.inner_text().strip()[:120]
    return errors


@tool("browser_snapshot", "Compact view of the page: text + interactive elements", {"app": "mail|finance"})
def browser_snapshot(app):
    """Describe the current page in a few hundred words the model can read.

    Why not just give the model the page's HTML? It is huge, noisy and costs a lot of tokens. A snapshot
    keeps only what the model needs to decide what to do next: what it says, what can be clicked, which
    messages are showing, whether a popup covers the page.
    """
    error = prepare(app)
    if error is not None:
        return error
    page = get_session().pages[app]

    if page.url == "about:blank":
        return ToolResult.fail("ERR_NAV_FAILED", f"nothing is open in {app} yet, use browser_open")
    if page_is_blank(page):
        return ToolResult.fail("ERR_BLANK_PAGE", "page came back empty, reload it")

    elements, more = read_elements(page)
    text = " ".join(page.inner_text("body").split())        # split() + join() squeezes all whitespace into single spaces

    data = {
        "url": page.url,
        "title": page.title(),
        "text": text[:TEXT_CHARS],
        "elements": elements,
        "flashes": read_flashes(page),
        "errors": read_form_errors(page),
        "banner": banner_is_visible(page),
    }
    if more:
        data["more_elements"] = True       # the page has more than MAX_ELEMENTS things; the list was cut
    return ToolResult(True, data, evidence=f"snapshot of {page.url}")


# =====================================================================================================
# TOOL 3: browser_click
# =====================================================================================================
@tool("browser_click", "Click an element by testid or visible text", {"app": "mail|finance", "target": "str"})
def browser_click(app, target):
    """Click something. `target` is a data-testid (preferred) or some visible text.

    Three things can go wrong, and each gets its own error code:
      - nothing matches the target            -> ERR_NO_ELEMENT
      - a popup covers the page               -> ERR_INTERCEPTED (the agent must click banner-dismiss first)
      - the click works but the next page is broken (500, login page...) -> the codes from check_page()
    """
    error = prepare(app)
    if error is not None:
        return error
    session = get_session()
    page = session.pages[app]

    element = find_element(page, target)
    if element is None:
        return no_element_error(target)

    # While the popup is up, the only thing that may be clicked is the popup's own "Got it" button.
    # We say so immediately instead of letting Playwright wait 10 seconds and then fail.
    if banner_is_visible(page) and element.get_attribute("data-testid") != "banner-dismiss":
        return ToolResult.fail("ERR_INTERCEPTED", "blocked by maintenance-banner, click banner-dismiss first")

    path_before = urlparse(page.url).path
    session.forget_status(app)
    try:
        element.click()
    except Exception as exc:
        message = str(exc)
        if "intercepts pointer events" in message:
            # Playwright's message names the element that is in the way, e.g. data-testid="some-popup".
            found = re.search(r'data-testid="([^"]+)"', message)
            blocker = found.group(1) if found else "another element"
            return ToolResult.fail("ERR_INTERCEPTED", f"blocked by {blocker}")
        return action_error(exc)

    try:
        page.wait_for_load_state("load")                   # a click may start loading a new page; wait for it
    except Exception as exc:
        return action_error(exc)

    # If we were ON the login page before the click, being on it afterwards is not "expired".
    problem = check_page(app, page, expect_login=(path_before == "/login"))
    if problem is not None:
        # Keep what the page said. After a submit this is how a "Invoice saved" message that the
        # server shows even though nothing was saved stays visible next to the error.
        flashes = read_flashes(page)
        if len(flashes) > 0:
            problem.error_message = problem.error_message + " (page said: " + " | ".join(flashes) + ")"
        return problem

    data = {
        "clicked": target,
        "url": page.url,
        "status": session.last_status[app],
        "flashes": read_flashes(page),
    }
    return ToolResult(True, data, evidence=f"clicked {target}, now at {page.url}")


# =====================================================================================================
# TOOL 4: browser_fill
# =====================================================================================================
@tool("browser_fill", "Type into a field", {"app": "mail|finance", "target": "str", "value": "str"})
def browser_fill(app, target, value):
    """Type `value` into a text box, then read the box back to make sure it really holds that text.

    The read-back catches silent problems: text cut off by a length limit, or typed into the wrong box.
    """
    error = prepare(app)
    if error is not None:
        return error
    page = get_session().pages[app]

    element = find_element(page, target)
    if element is None:
        return no_element_error(target)

    try:
        element.fill(str(value))                           # fill() clears the box first, then types
        actual = element.input_value()
        is_password = element.get_attribute("type") == "password"
    except Exception as exc:
        return action_error(exc)

    if actual != str(value):
        return ToolResult.fail("ERR_VALIDATION", f"typed into {target} but it holds '{actual}'")

    shown = "***" if is_password else actual              # never put a password into logs or prompts
    return ToolResult(True, {"target": target, "value": shown}, evidence=f"{target} = {shown}")


# =====================================================================================================
# TOOL 5: browser_select
# =====================================================================================================
@tool("browser_select", "Choose a dropdown option", {"app": "mail|finance", "target": "str", "option": "str"})
def browser_select(app, target, option):
    """Choose an entry in a dropdown (<select>), e.g. the Vendor list on the new-invoice form.

    `option` can be the text people see ("Company X Ltd") or the hidden value ("V001").
    """
    error = prepare(app)
    if error is not None:
        return error
    page = get_session().pages[app]

    element = find_element(page, target)
    if element is None:
        return no_element_error(target)

    # Read every option as a (value, label) pair.
    choices = []
    for choice in element.locator("option").all():
        value = choice.get_attribute("value") or ""
        label = choice.inner_text().strip()
        choices.append((value, label))

    # First look for a matching label, then for a matching value.
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
        # Tell the model what IS available. That is much better than just saying "no".
        shown = ", ".join(f"{value or '-'}={label}" for value, label in choices[:20])
        return ToolResult.fail("ERR_VALIDATION", f"no option '{option}' in {target}. Options: {shown}")

    try:
        element.select_option(value=match[0])
    except Exception as exc:
        return action_error(exc)

    data = {"target": target, "selected": match[1], "value": match[0]}
    return ToolResult(True, data, evidence=f"{target} = {match[1]} ({match[0]})")


# =====================================================================================================
# TOOL 6: browser_download
# =====================================================================================================
@tool("browser_download", "Download an attachment to the run folder", {"app": "mail|finance", "target": "str"})
def browser_download(app, target):
    """Save the file a link points to (the invoice PDF) into the run's download folder.

    How: we do not click and wait for the browser's download event (fiddly). We read the link's address
    and fetch it with the browser's own request client, which sends the same login cookie. As a bonus
    we get the HTTP status directly, so a missing file (404) is easy to recognise.
    """
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

    # urljoin turns "/email/1/attachment.pdf" into a full address. It must stay on the same site.
    url = urljoin(page.url, link)
    if not url.startswith(session.base_urls[app] + "/"):
        return ToolResult.fail("ERR_POLICY", "download link points outside the allowed site")

    try:
        response = session.context.request.get(url)
        body = response.body()                             # the file's bytes
    except PlaywrightTimeout:
        return ToolResult.fail("ERR_TIMEOUT", "download timed out")
    except Exception as exc:
        time.sleep(RETRY_PAUSE_SECONDS)
        return ToolResult.fail("ERR_NAV_FAILED", first_line(exc))

    if urlparse(response.url).path == "/login":            # the server redirected us to sign in
        return ToolResult.fail("ERR_SESSION_EXPIRED", "download redirected to the login page")
    problem = http_error(response.status, response.headers.get("retry-after"), "download")
    if problem is not None:
        return problem                                     # e.g. 404 when the file is missing
    content_type = response.headers.get("content-type", "").lower()
    if "pdf" not in content_type and not body.startswith(b"%PDF"):   # every real PDF starts with the bytes %PDF
        return ToolResult.fail("ERR_DOWNLOAD", "the response is not a PDF")

    # Work out the file name: from the server's Content-Disposition header if there is one, else the URL.
    header = response.headers.get("content-disposition", "")
    found = re.search(r'filename="?([^";]+)"?', header)
    if found:
        name = Path(found.group(1)).name                   # .name drops any folders, so no "../" tricks
    else:
        name = Path(urlparse(url).path).name
    if name == "":
        name = "download.pdf"

    # Never overwrite an earlier download. If INV-1.pdf exists, save INV-1-2.pdf, then INV-1-3.pdf...
    # (After a corrupt first download, the re-download must not hide the broken one.)
    session.download_dir.mkdir(parents=True, exist_ok=True)
    destination = session.download_dir / name
    counter = 2
    while destination.exists():
        destination = session.download_dir / f"{Path(name).stem}-{counter}{Path(name).suffix}"
        counter = counter + 1

    destination.write_bytes(body)
    # sha256 is a fingerprint of the file's bytes. It proves later exactly which file was processed.
    fingerprint = hashlib.sha256(body).hexdigest()
    data = {"path": str(destination), "size": len(body), "sha256": fingerprint}
    return ToolResult(True, data, evidence=f"{destination.name}, {len(body)} bytes, sha256 {fingerprint[:12]}")


# =====================================================================================================
# TOOL 7: browser_login
# =====================================================================================================
@tool("browser_login", "Log in with sandbox credentials from config", {"app": "mail|finance"})
def browser_login(app):
    """Sign in to one of the sites.

    Notice there is no username or password argument. The credentials come from the config, so the model
    never sees them and can never leak or change them (design rule 4).
    """
    error = prepare(app)
    if error is not None:
        return error
    session = get_session()
    page = session.pages[app]

    if app == "mail":
        username, password = session.cfg.mail_user, session.cfg.mail_password
    else:
        username, password = session.cfg.finance_user, session.cfg.finance_password

    # Always start from a fresh /login page. If we were sent here because the session expired, the
    # old "?expired=1" address is simply replaced. (browser_open already maps load errors to codes.)
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

    # After a successful login the site redirects to the inbox / invoice list. Check that page too.
    problem = check_page(app, page, expect_login=True)
    if problem is not None:
        return problem

    # Still on the login page means the login did not work. Why?
    if urlparse(page.url).path == "/login":
        message = " ".join(read_flashes(page))
        if "temporarily unavailable" in message.lower():
            # The correct password was rejected because the service hiccuped (login_flaky_once fault).
            # That is NOT a wrong password, so tell the loop it may retry.
            return ToolResult.fail("ERR_LOGIN_TRANSIENT", message)
        if message == "":
            message = "still on the login page"
        return ToolResult.fail("ERR_LOGIN_FAILED", message)

    return ToolResult(True, {"logged_in": True, "app": app, "url": page.url}, evidence=f"logged in to {app}")
