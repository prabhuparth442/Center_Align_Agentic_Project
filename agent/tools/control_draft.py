"""DRAFT of agent/tools/control.py with all five tools written out in plain, readable Python.
Read it top to bottom, then copy what you understand into control.py. Delete this file afterwards.

Nothing imports this file. It is only here to learn from.

=====================================================================================================
WHAT THIS FILE IS FOR
=====================================================================================================
The browser, document and finance tools act on the OUTSIDE world. The five "control" tools act on the
AGENT'S OWN RUN:

    remember          write a fact down (with where it was seen)
    recall            read the facts back
    ask_human         ask a person a question (a scripted "oracle" answers during evaluation)
    request_approval  ask permission before a risky action
    finish            end the run

=====================================================================================================
THE PROBLEM THEY SOLVE, AND HOW
=====================================================================================================
These tools need the current run's RunState (state.py): its memory of facts, whether the verifier has
passed, its status. But a tool is just a function that the loop calls with the arguments the model chose
(browser_open("mail", "/inbox")). The model cannot, and must not, pass the RunState itself.

The solution used here: the loop calls bind(state, trace, oracle) ONCE at the start of a run. bind() stores
those objects in a module-level variable (`current`, below). Every tool then reaches the run through
`current`. In loop.py this is the single line:

    control.bind(state, trace)

If a tool is used before bind() has been called, that is a mistake in OUR program (not something the model
did), so we raise an ordinary error instead of returning a polite failure. It should be loud.

=====================================================================================================
THE ORACLE
=====================================================================================================
ask_human and request_approval need somebody to answer. In a real run that is a person. In evaluation there
is nobody, so a scripted function (the "oracle") answers with prepared answers. It is just a function:
    oracle(question, options) -> answer text
If NO oracle was given, nobody can be asked. The run then stops with status needs_human and the tool
returns ERR_NEEDS_HUMAN (checker.py turns that into "escalate"). Stopping is the safe choice in a finance
system. Guessing is not.
"""
from .base import ToolResult, tool

# The only allowed ways for a run to end.
STATUSES = ["success", "partial", "failed", "needs_human"]


class RunContext:
    """A small box holding what the control tools need. There is exactly one, called `current`."""

    def __init__(self):
        self.state = None      # the RunState of this run (facts, status, ...)
        self.trace = None      # the Trace (log file). Optional.
        self.oracle = None     # function(question, options) -> answer. Optional.


current = RunContext()


def bind(state, trace=None, oracle=None):
    """Called once by the loop at the start of a run. Gives the tools access to the run."""
    current.state = state
    current.trace = trace
    current.oracle = oracle


def get_state():
    """The RunState, or a loud error if bind() was forgotten."""
    if current.state is None:
        raise RuntimeError("control tools used before control.bind(state) was called")
    return current.state


def ask_oracle(question, options):
    """Ask whoever answers questions. Returns the answer, or None if there is nobody to ask."""
    state = get_state()
    if current.oracle is None:
        state.status = "needs_human"       # nobody can decide, so the run stops here
        return None
    return str(current.oracle(question, options))


def nobody_to_ask():
    return ToolResult.fail("ERR_NEEDS_HUMAN", "no human is available to answer, the run stops as needs_human")


# =====================================================================================================
# remember / recall: the run's memory
# =====================================================================================================
# The model forgets between steps. Anything it needs later (the invoice number, the amount...) must be
# written down with remember(). Each fact stores WHERE it was seen (the "evidence"), for example
# "INV-X-1042.pdf: 'Total Payable: INR 98,400.00'". That is provenance: every value can be traced to its
# source, which is what makes the final report believable.
#
# The description below is the text the model reads when it chooses a tool. It carries two rules that
# other files depend on, so the model has to be told them here:
#   1. use exactly these keys for an invoice (verifier.py compares these keys with the finance record)
#   2. start the evidence with "inferred:" or "guess:" when the value was NOT read directly from the
#      document. policy.py looks for those words and then asks for approval before submitting.
@tool("remember", "Store a fact with its evidence. Invoice keys: invoice_no, vendor_id, amount, "
                  "invoice_date, due_date. Start evidence with 'inferred:' or 'guess:' if the value "
                  "was not read directly from the document",
      {"key": "str", "value": "str", "evidence": "str"})
def remember(key, value, evidence):
    state = get_state()
    if str(evidence).strip() == "":
        # No evidence, no fact. policy.py also refuses to submit a value that has none.
        return ToolResult.fail("ERR_VALIDATION", "evidence is required: say where the value was seen")
    state.remember(key, str(value), str(evidence))
    return ToolResult(True, {"key": key, "value": str(value)}, evidence=str(evidence))


@tool("recall", "Read stored facts", {"key": "str?"})
def recall(key=None):
    """One fact if a key is given ("str?" means optional), otherwise all facts."""
    state = get_state()
    if key:
        fact = state.recall(key)                           # returns the Fact, or None if there is no such key
        if fact is None:
            return ToolResult.fail("ERR_VALIDATION", f"no fact '{key}'. Stored: {list(state.memory)}")
        return ToolResult(True, {key: {"value": fact.value, "evidence": fact.evidence}})

    everything = {}
    for name, fact in state.memory.items():
        everything[name] = {"value": fact.value, "evidence": fact.evidence}
    return ToolResult(True, everything)


# =====================================================================================================
# ask_human / request_approval: the human in the loop
# =====================================================================================================
@tool("ask_human", "Ask a question and wait (scripted oracle in eval)", {"question": "str", "options": "list?"})
def ask_human(question, options=None):
    """Use this when the agent really cannot decide, e.g. two vendors match the name, or the PDF is
    unreadable twice. Asking is safer than guessing in a finance system."""
    answer = ask_oracle(question, options)
    if answer is None:
        return nobody_to_ask()
    return ToolResult(True, {"answer": answer}, evidence=f"asked '{question}' -> '{answer}'")


@tool("request_approval", "Ask approval before a risky write", {"action_summary": "str", "risk": "str"})
def request_approval(action_summary, risk):
    """Ask permission before a risky write. policy.py demands it when a value was guessed or inferred,
    the amount is above APPROVAL_THRESHOLD_INR, or the invoice is past due. `risk` says why.

    If the answer is yes, we store the fact approval="yes". That fact is the signal policy.py looks for;
    with it present, the submit is allowed. If the answer is no, nothing is stored and the model should
    finish with status needs_human.
    """
    state = get_state()
    answer = ask_oracle(f"Approve this action? {action_summary} (risk: {risk})", ["yes", "no"])
    if answer is None:
        return nobody_to_ask()

    # "yes", "Yes", "y", "approved" all count as a yes. startswith() accepts a tuple of choices.
    approved = answer.strip().lower().startswith(("y", "approv"))
    if approved:
        state.remember("approval", "yes", f"approved: {action_summary}")

    outcome = "granted" if approved else "refused"
    return ToolResult(True, {"approved": approved}, evidence=f"approval {outcome}: {action_summary}")


# =====================================================================================================
# finish: the only way a run ends
# =====================================================================================================
@tool("finish", "End the run. 'success' is refused unless the verifier passed",
      {"status": "success|partial|failed|needs_human", "summary": "str", "evidence": "list"})
def finish(status, summary, evidence):
    """End the run with one of: success, partial, failed, needs_human.

    THE SAFETY RULE: "success" is only accepted after the verifier has read the saved invoice back from
    the finance system and found every field matching. The model cannot simply CLAIM success. So when it
    asks for "success" and the verifier has not passed yet, this tool runs the verifier itself. If the
    read-back fails, success is refused (ERR_POLICY, with the list of differences) and the run keeps
    going, so the model can fix the problem or finish honestly as failed / needs_human.
    """
    state = get_state()
    if status not in STATUSES:
        return ToolResult.fail("ERR_VALIDATION", f"status must be one of {STATUSES}")

    if status == "success" and not state.verified:
        # Imported here and not at the top of the file: verifier.py uses the tools, and the tools use
        # verifier.py. Importing at the top could make the two wait for each other forever.
        from ..verifier import verify_invoice
        report = verify_invoice(state, current.trace)
        if not report.passed:
            differences = "; ".join(report.mismatches)
            return ToolResult.fail("ERR_POLICY", "success refused, read-back failed: " + differences)

    state.status = status                                  # the loop stops as soon as this is not "running"
    if current.trace is not None:
        current.trace.log("finish", status=status, summary=summary, evidence=evidence)
    return ToolResult(True, {"status": status, "summary": summary}, evidence=f"run finished: {status}")
