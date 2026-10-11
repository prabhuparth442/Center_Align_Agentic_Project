"""DRAFT of agent/loop.py, with every TODO filled in. Read it, then copy what you understand into loop.py.
Delete this file afterwards.

The idea in one picture (this is what the while loop does on every turn):

    pick current step
        -> executor: LLM chooses ONE tool call
        -> policy:   code says allow / needs_approval / block
        -> run the tool
        -> checker:  code says ok / retry / recover / replan / escalate
        -> loop reacts to that verdict (this is the part that was TODO)
    ... until the plan is done, a budget runs out, or someone sets state.status
    then: verifier (read-back) decides if "success" is allowed.
"""
# =====================================================================================
# LEARNING NOTES + REVIEW. Only comments were added to this file, the code is unchanged.
#
# Words used below
#   tool call    one action the model asks for, e.g. browser_open(app="mail", path="/inbox")
#   step         one item of the plan: {intent, expect}. In this draft ONE step = ONE tool call.
#   decision     policy.py's answer about a tool call BEFORE it runs: allow / needs_approval / block
#   verdict      checker.py's answer about a tool result AFTER it ran: ok / retry / recover / replan / escalate
#   observation  the short text the executor sees on the next turn as "Last observation"
#   trace        the append-only log file (trace.jsonl). The run report is built from it.
#
# How to read the file: run() is the story. The small helpers above it (recover, compact, run_tool,
# track_submits) are pieces run() calls, and try_replan() at the bottom is the replan helper.
#
# REVIEW: things to fix or decide before you copy this into loop.py. Most important first.
#
#  1. A STEP IS NOT A TOOL CALL (design mismatch, will make S1 end early).
#     The loop moves to the next step after EVERY successful tool call (step_index += 1). But the
#     planner prompt says "Max 12 steps" (planner.py) and ARCHITECTURE.md says "3-8 steps", while the main
#     task needs about 30 tool calls (login, open, search, click, download, read_pdf, 5 grounded_checks,
#     5 remembers, vendors, get_invoice, login, open, select, 4 fills, submit, read-back, finish).
#     With a 8-step plan the loop would use up the plan after 8 calls and fall into the "after the
#     loop" block half way through the task. Pick ONE of these:
#       a) make the plan fine-grained: change the planner prompt to "one tool call per step, up to
#          ~35 steps", and raise MAX_STEPS above 40 (retries and replans also use up steps_used), or
#       b) let one step take several tool calls: only do step_index += 1 when the step's `expect` is
#          really met (the checker's TODO says this too). More work, more flexible.
#     (a) is the simple one.
#
#  2. replan() CRASHES (bug in planner.py, not here). planner.py line ~79 writes REPLAN_PROMPT(...) but
#     REPLAN_PROMPT is a string, not a function. It must be REPLAN_PROMPT.format(...). Until then the
#     first replan raises TypeError and try_replan() below takes the whole run down with it.
#
#  3. track_submits() ONLY RECORDS A SUBMIT WHEN result.ok. Think about save_then_500_once: the invoice
#     IS saved but the click returns ERR_HTTP_5XX, so result.ok is False, nothing is added to
#     state.submitted, the checker says "retry", and the form is submitted a second time. The design rule
#     is: after a submit that was not clean, read back before trying again. Add the number to
#     state.submitted whenever the submit click was ATTEMPTED (drop `and result.ok`). Policy then blocks
#     a second submit until finance_get_invoice has said ERR_NOT_FOUND.
#
#  4. THE "recover" BRANCH CAN SPIN. recover("dismiss_overlay") does nothing (it says so in its note), and
#     the recover branch never counts against state.retries. A popup then repeats: intercepted, "recover",
#     intercepted... until max_steps. Two fixes: browser_click(app, "banner-dismiss") now EXISTS, so do the
#     real dismiss; and give recoveries a small limit of their own.
#
#  5. relogin MAY LOG IN TO THE WRONG APP. `app` is the last app seen in an action's args. finance_get_invoice
#     has no `app` argument, so after a mail step the session-expired recovery would log in to mail. Work out
#     the app from the tool name too (names starting with finance_ mean the finance app).
#
#  6. ERRORS AND CLEAN-UP.
#     - Only ValueError from next_action is caught. LLMError (API down, rate limit) is not, and make_plan()
#       and replan() are not protected at all. One bad network moment crashes the run with no report.
#     - Nothing closes the browser. Wrap the body of run() in try/finally and call browser.close_session()
#       in the finally. Also call browser.set_download_dir(trace.dir / "downloads") right after the Trace
#       is created, so each run keeps its own PDFs.
#
#  7. THE TRACE LACKS THE TOOL OUTPUT. "policy" and "check" lines are logged, but not what the tool
#     returned. Add trace.log("observation", tool=..., text=compact(result)) so the audit shows evidence.
#
#  8. SMALLER THINGS.
#     - After a failed read-back, status "partial" is a bit generous. "failed" is more honest.
#     - ARCHITECTURE.md says to honour Retry-After; the loop only uses the 1s / 2s backoff.
#     - try_replan is defined below run(). That works in Python (it is looked up when called),
#       but defining helpers first reads better.
# =====================================================================================
import time
import uuid

# backoff_seconds(n) -> 1 then 2 seconds; check() is the checker (decides ok/retry/recover/...)
from .checker import backoff_seconds, check
from .config import Config
from .executor import next_action
from .llm import LLM
from .planner import make_plan, replan
# is_submit(action) is True for "click submit-invoice on the finance app", the one write we have
from .policy import check as policy_check, is_submit
from .state import RunState
# control = remember / recall / ask_human / request_approval / finish. They need the RunState,
# which is why run() has to call control.bind(...) once.
from .tools import control
from .tools.base import REGISTRY, ToolResult
from .trace import Trace
from .verifier import verify_invoice

MAX_OBSERVATION_CHARS = 3000   # same limit the executor uses; keeps what we feed back small

# checker.py returns a *name* of a recovery ("dismiss_overlay", "relogin").
# Here we turn that name into a real tool call. Neither name is a registered tool,
# so they cannot go straight into REGISTRY.
def recover(name: str, app: str | None) -> ToolResult:
    # relogin: the session expired (the server sent us back to /login). Logging in again gives a fresh
    # session and the same step can be tried again. `app or "finance"` = use the last app seen,
    # otherwise guess finance. (REVIEW 5: that guess can be the wrong app.)
    if name == "relogin":
        return REGISTRY["browser_login"].fn(app=app or "finance")
    if name == "dismiss_overlay":
        # NOTE: the blocker's testid comes with ERR_INTERCEPTED (see browser.py TODO). Until that is
        # wired in, we can only retry. Replace this with a browser_click on the blocker's testid.
        # (REVIEW 4: the tool exists now. The blocker is always "banner-dismiss":
        #  REGISTRY["browser_click"].fn(app=app, target="banner-dismiss").)
        return ToolResult(True, evidence="no overlay-dismiss tool yet; the step will simply be retried")
    # An unknown name means checker.py and this function disagree. Report it instead of hiding it.
    return ToolResult.fail("ERR_UNKNOWN", f"no such recovery: {name}")


def compact(result: ToolResult) -> str:
    """Turn a ToolResult into the short text the executor sees as 'Last observation'."""
    # The model only reads text, so the result is written as a string. `data` can be a big dict
    # (a snapshot), which is why the text is cut at the end.
    text = f"ok. data={result.data}"
    if result.evidence:
        text += f"\nevidence: {result.evidence}"
    return text[:MAX_OBSERVATION_CHARS]


def run_tool(name: str, args: dict) -> ToolResult:
    """Run a tool but never let an exception kill the run: turn it into a normal failure result."""
    # Why: the model chooses `args`, and a wrong or missing argument raises TypeError inside
    # fn(**args). Returning a failed ToolResult instead means the checker handles it like any
    # other error (ERR_UNKNOWN is not retryable, so it goes to "replan").
    try:
        return REGISTRY[name].fn(**args)
    except Exception as e:   # a bug in a tool, bad args, network down...
        return ToolResult.fail("ERR_UNKNOWN", f"{type(e).__name__}: {e}")


def track_submits(state: RunState, action, result: ToolResult) -> None:
    """policy.py wants state.submitted kept up to date (at most one submit per invoice number)."""
    # state.submitted is the duplicate-submit guard: policy.check_submit blocks a submit when the
    # invoice number is in it. Without this function it would stay empty and the guard does nothing.
    inv = state.memory.get("invoice_no")
    if inv is None:
        return
    # .upper() because the finance app stores invoice numbers in upper case, and policy compares
    # in upper case too.
    number = inv.value.strip().upper()
    # (REVIEW 3: `and result.ok` means a submit that errored, but still saved, is not recorded.)
    if is_submit(action.model_dump()) and result.ok:
        state.submitted.add(number)                     # we wrote it: never blindly write again
    # The read-back is how a number gets released again: "not found" = the save did not land,
    # so submitting once more is safe.
    elif action.tool == "finance_get_invoice" and result.error_code == "ERR_NOT_FOUND":
        state.submitted.discard(number)                 # read-back says it did not land: retry is safe


def run(goal: str, cfg: Config | None = None) -> RunState:
    # cfg: the settings (limits, URLs). Tests can pass their own; otherwise read env variables.
    cfg = cfg or Config.from_env()
    # RunState is the one object that carries the run from turn to turn: goal, plan, step_index,
    # memory (facts the model remembered), counters (steps_used, retries, replans) and status.
    # run_id (8 random hex characters) names the folder runs/<run_id>/ that holds the trace.
    state = RunState(goal=goal, run_id=uuid.uuid4().hex[:8])
    trace = Trace(cfg.run_dir, state.run_id)
    llm = LLM(cfg.llm_base_url, cfg.llm_api_key, cfg.planner_model)  # same model, think toggled per call
    # (REVIEW 6: browser.set_download_dir(trace.dir / "downloads") belongs here, and everything below
    #  should sit inside try/finally with browser.close_session() in the finally.)

    # TODO "bind RunState for control tools" -> one line. After this, remember / recall / ask_human /
    # request_approval / finish can all see this run's state. No oracle given: asking a human stops
    # the run as needs_human (an eval harness would pass its scripted oracle here).
    control.bind(state, trace)

    # The planner (thinking ON) turns the goal into Steps. Each Step has `intent` (what to do) and
    # `expect` (what should be true afterwards). (REVIEW 6: this call can raise, it is unprotected.)
    plan = make_plan(llm, state)
    # Changed: keep the Step objects, not just their text. replan() reads step.intent and step.expect
    # from state.plan, and trace.report() accepts either.
    state.plan = list(plan.steps)
    trace.log("plan", steps=[s.model_dump() for s in plan.steps])

    # What the executor sees as "Last observation". Nothing has happened yet on the first turn.
    observation = "(start)"
    app = None   # last app the model touched; used if we must re-login

    # Each pass of this loop = ONE tool call for the CURRENT step. It ends when the plan is used up
    # or too many tool calls were made (cfg.max_steps, 40 by default; REVIEW 1).
    while state.step_index < len(state.plan) and state.steps_used < cfg.max_steps:
        # Stop conditions first: a finish() call, or the token budget.
        # The finish tool (and ask_human without an oracle) change state.status from "running". This
        # check is what ends the loop after them.
        if state.status != "running":
            break
        # Stop cleanly when the model calls have cost more tokens than allowed. The trace line is what
        # lets the report say WHY the run stopped.
        if llm.usage.total > cfg.token_budget:
            state.status = "failed"
            trace.log("budget", reason="token budget exceeded", used=llm.usage.total)
            break

        # The plan step we are working on right now.
        step = state.plan[state.step_index]

        # 1) Executor: ask the LLM for one tool call. It can raise if the model keeps naming tools
        #    that do not exist. Count that as a failed step and replan.
        #    next_action() already retried inside (bad JSON, unknown tool name), so a ValueError here
        #    means the model really could not produce a valid call.
        try:
            action = next_action(llm, state, step, observation)
        except ValueError as e:
            state.steps_used += 1                  # counts, so this cannot repeat forever
            # try_replan returns False when the replan budget is used up; the status is then already
            # needs_human, so we just leave the loop.
            if not try_replan(llm, state, trace, cfg, f"executor failed: {e}"):
                break
            observation = f"(replanned after: {e})"
            continue                               # `continue` = jump back to the top of the while loop
        # Remember the app of this call (args.get(key, default) keeps the old value if there is none).
        app = action.args.get("app", app)

        # 2) Policy: code decides whether this action may run at all.
        #    It needs a plain dict, which is why model_dump() turns the pydantic Action into one.
        decision = policy_check(action.model_dump(), state, cfg)
        trace.log("policy", action=action.model_dump(), verdict=decision.verdict, reason=decision.reason)

        if decision.verdict == "block":
            # Do not run it. Tell the model why, and it picks a different action on the next turn
            # (steps_used still counts, so a model that keeps trying cannot loop forever).
            state.steps_used += 1
            observation = f"BLOCKED by policy: {decision.reason}. Choose a different action."
            continue

        if decision.verdict == "needs_approval":
            # Ask for approval ourselves instead of hoping the model does. request_approval stores the
            # fact approval="yes" on a yes, and that is what makes policy allow the submit.
            ask = REGISTRY["request_approval"].fn(
                action_summary=f"{action.tool} {action.args}", risk=decision.reason)
            trace.log("approval", reason=decision.reason, ok=ask.ok, data=ask.data)
            # Not ok = nobody could be asked (no oracle). Not approved = a human said no. Either way
            # we stop and hand over; we never carry on with a risky write on a guess.
            if not ask.ok or not ask.data.get("approved"):
                state.status = "needs_human"       # no oracle, or the human said no: stop, never guess
                break
            # approved -> fall through and run the action

        # 3) Run the tool.
        #    run_tool never raises, so one broken tool call cannot end the whole run.
        result = run_tool(action.tool, action.args)
        state.steps_used += 1
        track_submits(state, action, result)

        # 4) Checker: code decides what the result means.
        #    state.retries = retries used by THIS step so far; cfg.max_retries is the limit.
        verdict = check(result, step.expect, state.retries, cfg.max_retries)
        trace.log("check", tool=action.tool, ok=result.ok, error=result.error_code, verdict=verdict.kind)
        # (REVIEW 7: also log the tool output here, e.g. trace.log("observation", tool=..., text=compact(result)).)

        if state.status != "running":      # the tool itself ended the run (finish, or no human available)
            break

        # 5) React to the verdict. This was the big TODO.
        if verdict.kind == "ok":
            state.step_index += 1          # next step
            state.retries = 0              # the retry counter is per step
            # A not-found answer is "ok" for the checker but is not a normal result; say so plainly.
            observation = compact(result) if result.ok else f"not found ({result.error_message})"

        elif verdict.kind == "retry":
            # A temporary problem (500, dropped connection, blank page...). Wait, then do the same
            # step again. The wait grows with each retry; when retries reach the limit the checker
            # itself answers "escalate", so this branch cannot run forever.
            time.sleep(backoff_seconds(state.retries))   # 1s, then 2s
            state.retries += 1
            observation = f"{action.tool} failed with {result.error_code}; trying again. {result.error_message}"

        elif verdict.kind == "recover":
            # Something is in the way (popup, expired session). Fix it, then do the same step again.
            # step_index is NOT increased. (REVIEW 4: nothing here counts against a limit.)
            fix = recover(verdict.detail, app)           # e.g. log in again, then do the same step again
            trace.log("recover", name=verdict.detail, ok=fix.ok, error=fix.error_code)
            observation = f"{result.error_code}: ran '{verdict.detail}' ({'ok' if fix.ok else 'failed'}). Redo the step."

        elif verdict.kind == "replan":
            # The plan itself looks wrong (validation error, 404...). Ask the planner for new remaining
            # steps, within the replan budget (cfg.max_replans, 2).
            reason = f"{verdict.detail} | {result.error_message}"
            if not try_replan(llm, state, trace, cfg, reason):
                break
            observation = f"(replanned after: {reason})"

        else:  # "escalate"
            # Cannot go on safely (retries used up, or a human decision is needed).
            state.status = "needs_human"
            trace.log("escalate", reason=verdict.detail)
            break

    # --- After the loop: decide the final status. ---
    # We only get here with status still "running" if the loop ended by itself: either the plan
    # was used up, or max_steps was hit. Every other exit above has already set a status.
    if state.status == "running":
        if state.step_index < len(state.plan):
            state.status = "failed"        # ran out of steps (max_steps)
        elif "invoice_no" in state.memory:
            # Plan finished but the model never called finish(). Success is only allowed after the
            # read-back passes. verify_invoice also sets state.verified and writes the "verify" trace line.
            report = verify_invoice(state, trace)
            state.status = "success" if report.passed else "partial"
        else:
            state.status = "partial"       # nothing to read back (e.g. a pure lookup task): not claiming success

    # Write report.md from the trace and the state, then give the finished state back to the caller
    # (cli.py prints state.status).
    trace.report(state, tokens=llm.usage.total)
    return state


def try_replan(llm: LLM, state: RunState, trace: Trace, cfg: Config, reason: str) -> bool:
    """Replace the remaining steps with a new plan. Returns False when the run must stop."""
    # Budget first: after cfg.max_replans (2) the run stops with evidence instead of looping on new plans.
    if state.replans >= cfg.max_replans:
        state.status = "needs_human"       # replan budget used up (ARCHITECTURE.md): stop with evidence
        trace.log("escalate", reason=f"replan budget exhausted: {reason}")
        return False
    # (REVIEW 2: replan() currently crashes on its first call, see planner.py.)
    new_plan = replan(llm, state, reason)  # replan() also does state.replans += 1
    # Keep the finished steps (replan() reads them as 'completed'), swap in the new remaining ones.
    # state.plan[:n] = the first n items of the list. Python slices never go out of range.
    state.plan = state.plan[:state.step_index] + list(new_plan.steps)
    state.retries = 0
    trace.log("replan", reason=reason, steps=[s.model_dump() for s in new_plan.steps])
    return True
