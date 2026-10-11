"""Control tools. Several are handled by the loop itself (they need RunState).

WHY THIS FILE EXISTS
--------------------
The browser/document/API tools act on the OUTSIDE world. These "control" tools act on the
AGENT'S OWN RUN: remembering facts, asking a person, asking for permission, and ending.

WHY THEY WERE STUBS THAT `raise NotImplementedError`
----------------------------------------------------
Each needs access to the current run's state (RunState in state.py: stored facts, whether the
verifier passed, the audit trace...). A plain decorated function has no access to that, so
loop.py will intercept these tool names and handle them itself with the state attached
(see the TODO in loop.py: "bind RunState for control tools"). They are still *registered* here
so they appear in the tool menu shown to the LLM (describe_tools in base.py).

Status: all five tools are implemented. Instead of the loop intercepting the names, it calls
bind(state, trace) once at the start of a run and the tools use what was bound (CTX below). The TODO
comments stay, with a short DONE note under each.
"""
from .base import ToolResult, tool

STATUSES = ["success", "partial", "failed", "needs_human"]

class Context:
    """What the control tools can reach. bind() fills it in."""

    def __init__(self):
        self.state = None     # the RunState of this run
        self.trace = None     # the Trace, optional
        self.oracle = None    # function(question, options) -> answer. A person, or the scripted oracle in eval


CTX = Context()


def bind(state, trace=None, oracle=None):
    CTX.state = state
    CTX.trace = trace
    CTX.oracle = oracle


def get_state():
    if CTX.state is None:
        # a mistake in our own code (bind was forgotten), not something the model did
        raise RuntimeError("control tools used before control.bind(state) was called")
    return CTX.state


def ask_oracle(question, options):
    """The answer, or None when there is nobody to ask. Then the run stops as needs_human."""
    state = get_state()
    if CTX.oracle is None:
        state.status = "needs_human"
        return None
    return str(CTX.oracle(question, options))


def nobody_to_ask():
    return ToolResult.fail("ERR_NEEDS_HUMAN", "no human is available to answer, the run stops as needs_human")


# --- Memory -----------------------------------------------------------------------------
# The model forgets between steps unless facts are stored. "evidence" records WHERE a value
# was seen (provenance), so later steps/audits can trace every fact back to its source.
#
# DONE: stores the fact. Evidence can't be empty (policy.py blocks a submit without it). The
# description is what the model reads, so it holds two rules other files rely on: the invoice keys
# verifier.py compares, and the "inferred:" / "guess:" prefix that makes policy.py ask for approval.
@tool("remember", "Store a fact with its evidence. Invoice keys: invoice_no, vendor_id, amount, "
                  "invoice_date, due_date. Start evidence with 'inferred:' or 'guess:' if the value "
                  "was not read directly from the document",
      {"key": "str", "value": "str", "evidence": "str"})
def remember(key, value, evidence):
    state = get_state()
    if str(evidence).strip() == "":
        return ToolResult.fail("ERR_VALIDATION", "evidence is required: say where the value was seen")
    state.remember(key, str(value), str(evidence))
    return ToolResult(True, {"key": key, "value": str(value)}, evidence=str(evidence))


# Reads facts back. `key=None` ("str?" = optional) returns everything stored.
#
# DONE: one fact with its evidence, or all of them. An unknown key lists the keys that exist.
@tool("recall", "Read stored facts", {"key": "str?"})
def recall(key=None):
    state = get_state()
    if key:
        fact = state.recall(key)
        if fact is None:
            return ToolResult.fail("ERR_VALIDATION", f"no fact '{key}'. Stored: {list(state.memory)}")
        return ToolResult(True, {key: {"value": fact.value, "evidence": fact.evidence}})

    everything = {}
    for name, fact in state.memory.items():
        everything[name] = {"value": fact.value, "evidence": fact.evidence}
    return ToolResult(True, everything)


# --- Human in the loop ------------------------------------------------------------------
# Used when the agent genuinely cannot decide (ambiguous vendor, corrupt PDF twice...).
# Safer to ask than to guess in a finance system. In evaluation there is no real person, so a
# scripted "oracle" (oracle_answers in the scenario file) answers deterministically.
#
# DONE: passes the question to the oracle and returns the answer. No oracle = nobody to ask, so the
# run is set to needs_human and the tool returns ERR_NEEDS_HUMAN (checker.py turns that into escalate).
@tool("ask_human", "Ask a question and wait (scripted oracle in eval)", {"question": "str", "options": "list?"})
def ask_human(question, options=None):
    answer = ask_oracle(question, options)
    if answer is None:
        return nobody_to_ask()
    return ToolResult(True, {"answer": answer}, evidence=f"asked '{question}' -> '{answer}'")


# Explicit permission step before the risky write. policy.py demands it when: the vendor was
# guessed, a value was inferred, the amount is over APPROVAL_THRESHOLD_INR, or the invoice is
# past due. `risk` explains to the approver why this needs a decision.
#
# DONE: asks yes/no through the oracle. On yes it stores the fact approval="yes", which is what
# policy.py looks for before it lets the submit through. On no it stores nothing.
@tool("request_approval", "Ask approval before a risky write", {"action_summary": "str", "risk": "str"})
def request_approval(action_summary, risk):
    state = get_state()
    answer = ask_oracle(f"Approve this action? {action_summary} (risk: {risk})", ["yes", "no"])
    if answer is None:
        return nobody_to_ask()

    # "yes", "y" and "approved" all count
    approved = answer.strip().lower().startswith(("y", "approv"))
    if approved:
        state.remember("approval", "yes", f"approved: {action_summary}")

    if approved:
        outcome = "granted"
    else:
        outcome = "refused"
    return ToolResult(True, {"approved": approved}, evidence=f"approval {outcome}: {action_summary}")


# --- Ending the run ---------------------------------------------------------------------
# The ONLY way a run ends. Status is one of: success | partial | failed | needs_human.
# Crucial safety rule: the loop REFUSES "success" unless verifier.py passed (i.e. the data was
# read back and matches). So the model cannot just *claim* it worked - code checks it.
# `evidence` is the list of proofs backing the claim (read-back record, quotes, statuses).
#
# DONE: for "success" it runs the verifier itself (unless it already passed), so the model can't skip
# the read-back. If that fails, success is refused and the run keeps going, so the model can fix it
# or finish honestly. The loop should stop once state.status is no longer "running".
@tool("finish", "End the run. 'success' is refused unless the verifier passed",
      {"status": "success|partial|failed|needs_human", "summary": "str", "evidence": "list"})
def finish(status, summary, evidence):
    state = get_state()
    if status not in STATUSES:
        return ToolResult.fail("ERR_VALIDATION", f"status must be one of {STATUSES}")

    if status == "success" and not state.verified:
        # imported here because verifier.py uses the tools, importing at the top would go in a circle
        from ..verifier import verify_invoice
        report = verify_invoice(state, CTX.trace)
        if not report.passed:
            return ToolResult.fail("ERR_POLICY", "success refused, read-back failed: " + "; ".join(report.mismatches))

    state.status = status
    if CTX.trace is not None:
        CTX.trace.log("finish", status=status, summary=summary, evidence=evidence)
    return ToolResult(True, {"status": status, "summary": summary}, evidence=f"run finished: {status}")
