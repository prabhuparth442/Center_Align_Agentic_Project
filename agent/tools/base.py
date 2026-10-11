"""Tool contract + registry. Every tool returns ToolResult with stable error codes.

WHY THIS FILE EXISTS
--------------------
The agent (an LLM) never touches the browser or the finance system directly. It only
*asks* for a "tool" by name, e.g. {"tool": "browser_open", "args": {"app": "mail"}}.
Our own Python code then runs that tool and hands the result back.
This file defines the three building blocks every tool shares:

  1. ToolResult      - the ONE shape every tool must return (so the loop can treat
                       all tools the same way and never has to guess).
  2. Tool            - a record describing a tool (name, what it does, its arguments,
                       the Python function behind it).
  3. REGISTRY + @tool - a global dictionary of all tools, plus a decorator that adds
                       a function to it automatically.

Read this file first; the other tool files just use these pieces.
"""
# `dataclass` auto-generates __init__/__repr__ for simple data-holding classes,
# so we don't have to write boilerplate.
from dataclasses import dataclass
# `Any` = "any type allowed"; `Callable` = "something you can call like a function".
# Used only for type hints (they help readers and IDEs, Python doesn't enforce them).
from typing import Any, Callable


@dataclass
class ToolResult:
    """What every tool returns. A uniform shape means the agent loop can do
    `if result.ok: ... else: branch on result.error_code` for ANY tool.

    This matches design rule 2 in docs/TOOLS.md: {ok, data, error_code, error_message, evidence}.
    """
    ok: bool                        # True = tool worked, False = it failed
    data: Any = None                # the useful payload on success (page text, record, path...)
    # Stable machine-readable error name. "Stable" matters: the loop does things like
    # `if error_code == "ERR_HTTP_5XX": retry`, so the names must never change randomly.
    # This avoids asking the LLM "what does this error mean?" (slow, unreliable, costly).
    error_code: str | None = None   # ERR_HTTP_5XX, ERR_NAV_FAILED, ... (see docs/TOOLS.md)
    error_message: str = ""         # human-readable detail, for logs and for the model
    # Proof of what happened (e.g. a quote from a PDF, a URL, an HTTP status). Evidence is
    # what lets us later *show* the result was real and not invented by the model.
    evidence: str = ""

    @classmethod
    def fail(cls, code, msg=""):
        """Shortcut to build a failure: ToolResult.fail("ERR_TIMEOUT", "page took >30s").
        `@classmethod` means it is called on the class itself (no instance needed) and
        `cls` is the class, so this is just `ToolResult(False, error_code=code, ...)`."""
        return cls(False, error_code=code, error_message=msg)


@dataclass
class Tool:
    """Metadata + implementation of one tool. One Tool object lives in REGISTRY per tool."""
    name: str                      # the exact string the LLM writes to call it
    description: str               # one line shown to the LLM so it knows WHEN to use the tool
    args: dict[str, str]           # arg name -> short type/description, shown to the model
    fn: Callable[..., ToolResult]  # the real Python function that does the work
    # Safety flag. Tools that CHANGE something in the outside world are "write" tools.
    # Those must be checked by policy.py (approval, idempotency, grounding) before running.
    # Read-only tools (snapshot, get_invoice...) skip that, because they can't do damage.
    writes: bool = False           # write tools always pass through policy.py


# The global tool directory: {"browser_open": Tool(...), "read_pdf": Tool(...), ...}
# It is filled automatically at import time by the @tool decorator below.
# loop.py looks tools up here: REGISTRY[action.tool].fn(**action.args)
REGISTRY: dict[str, Tool] = {}


def tool(name, description, args, writes=False):
    """Decorator that registers a function as a tool.

    Usage (see browser.py):
        @tool("browser_open", "Open a path in an app", {"app": "...", "path": "str"})
        def browser_open(app, path="/"): ...

    How it works: `tool(...)` is called first with the settings and returns `deco`.
    Python then calls `deco(browser_open)`, which stores the function in REGISTRY and
    returns it unchanged - so the function still works normally as plain Python too.
    Benefit: adding a new tool = writing one decorated function; no central list to edit.
    """
    def deco(fn):
        REGISTRY[name] = Tool(name, description, args, fn, writes)
        return fn
    return deco


def describe_tools() -> str:
    """Compact tool list injected into planner/executor prompts.

    The LLM can only use tools it knows exist. This builds a text menu such as:
        - browser_open(app: mail|finance, path: str): Open a path in an app
    which planner.py pastes into the prompt. Because it is generated from REGISTRY,
    the prompt can never get out of sync with the tools that really exist.
    """
    lines = []
    for t in REGISTRY.values():
        arg_parts = []
        for arg_name, arg_type in t.args.items():
            arg_parts.append(f"{arg_name}: {arg_type}")
        lines.append(f"- {t.name}({', '.join(arg_parts)}): {t.description}")
    return "\n".join(lines)
