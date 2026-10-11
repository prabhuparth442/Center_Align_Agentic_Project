"""Main loop: plan -> (act -> policy -> execute -> check)* -> verify -> finish."""
import time
import uuid

from .checker import check, backoff_seconds
from .config import Config
from .executor import next_action
from .llm import LLM
from .planner import make_plan, replan
from .policy import check as policy_check, is_submit
from .state import RunState
from .tools import control
from .trace import Trace
from .tools.base import REGISTRY, ToolResult
from .verifier import verify_invoice

MAX_OBSERVATION_CHARS = 3000

def recover(name: str, app: str | None) -> ToolResult:
    if name == "relogin":
        return REGISTRY["browser_login"].fn(app=app or "finance")
    if name == "dismiss_overlay":
        #TODO: Add this tool working part then update
        return ToolResult(True, evidence="no overlay-dismiss tool yet; the step will simply be retried")
    return ToolResult.fail("ERR_UNKNOWN", f"no such recovery: {name}")


def compact(result: ToolResult) -> str:
    """Turns a Toolresult inti a short text the executor sees as 'Last Observation"""
    text = f"ok. data = {result.data}"
    if result.evidence:
        text += f"\nevidence: {result.evidence}"
    return text[:MAX_OBSERVATION_CHARS]

def run_tool(name: str, args: dict) -> ToolResult:
    """Run a tool but never let an exception kill the run: turn it into normal failure result"""
    try:
        return REGISTRY[name].fn(**args)
    except Exception as e:
        return ToolResult.fail("ERR_UNKNOWN", f"{type(e).__name__}: {e}")


def track_submits(state: RunState, action, result: ToolResult)-> None:
    """policy.py wants state.submitted kept up to date (at most one submit per invoice number)."""

    inv = state.memory.get("invoice_no")
    if inv is None:
        return

    number = inv.value.strip().upper()

    if is_submit(action.model_dump()) and result.ok:
        state.submitted.add(number)
    elif action.tool == "finance_get_invoice" and result.error_code == "ERR_NOT_FOUND":
        state.submitted.discard(number)


def try_replan(llm : LLM, state: RunState, trace : Trace, cfg : Config, reason: str) -> bool:
    """Replace the remaining steps with a new plan. Returns False when the run must stop."""

    if state.replans >= cfg.max_replans:
        state.status = "needs_human"
        trace.log("escalate", reason = f"replan budget exhausted: {reason}")
        return False

    new_plan = replan(llm, state, reason)
    state.plan = state.plan[:state.step_index] + list(new_plan.steps)
    state.retries = 0
    trace.log("replan", reason=reason, steps=[s.model_dump() for s in new_plan.steps])
    return True


def run(goal: str, cfg: Config | None = None) -> RunState:
    cfg = cfg or Config.from_env()
    
    state = RunState(goal=goal, run_id=uuid.uuid4().hex[:8])
    trace = Trace(cfg.run_dir, state.run_id)
    llm = LLM(cfg.llm_base_url, cfg.llm_api_key, cfg.planner_model)  # same model, think toggled per call

    control.bind(state, trace)

    plan = make_plan(llm, state)
    state.plan = list(plan.steps)
    trace.log("plan", steps=[s.model_dump() for s in plan.steps])

    observation = "(start)"
    app = None

    while state.step_index < len(state.plan) and state.steps_used < cfg.max_steps:

        if state.status != "running":
            break

        if llm.usage.total > cfg.token_budget:
            state.status = "failed"
            trace.log("budget", reason = "token budget exceeded", used = llm.usage.total)
            break

        step = state.plan[state.step_index]

        try:
            action = next_action(llm, state, step, observation)
        except ValueError as e:
            state.steps_used += 1

            if not try_replan(llm, state, trace, cfg, f"executor failed : {e}"):
                break
            observation = f"(replanned after : {e})"
            continue

        app = action.args.get("app", app)

        # Policy Check
        decision = policy_check(action.model_dump(), state, cfg)
        trace.log("policy", action = action.model_dump(), verdict = decision.verdict, reason = decision.reason)

        if decision.verdict == "block":
            state.steps_used += 1
            observation = f"BLOCKED by policy : {decision.reason}. Choose a different action"
            continue

        if decision.verdict == "needs_approval":
            ask = REGISTRY["request_approval"].fn(
                action_summary = f"{action.tool} {action.args}",
                risk = decision.reason
            )
            trace.log("approval", reason = decision.reason, ok = ask.ok, data = ask.data)

            if not ask.ok or not ask.data.get("approved"):
                state.status = "needs_human"
                break

        # Run the tool

        result = run_tool(action.tool, action.args)
        state.steps_used += 1
        track_submits(state, action, result)

        # Checker
        verdict = check(result, step.expect, state.retries, cfg.max_retries)
        trace.log("check", tool=action.tool, ok=result.ok, error=result.error_code, verdict=verdict.kind)

        if state.status != "running":
            break

        # Reaction to the verdict
        if verdict.kind == "ok":
            state.step_index += 1
            state.retries = 0
            observation = compact(result) if result.ok else f"not found ({result.error_message})"

        elif verdict.kind == "retry":
            time.sleep(backoff_seconds(state.retries))
            state.retries += 1
            observation = f"{action.tool} failed with {result.error_code}; trying again. {result.error_message}"

        elif verdict.kind == "recover":
            fix = recover(verdict.detail, app)
            trace.log("recover", name = verdict.detail, ok = fix.ok, error= fix.error_code)
            observation = f"{result.error_code}: ran '{verdict.detail}' ({'ok' if fix.ok else 'failed'}). Redo the step."

        elif verdict.kind == "replan":
            reason = f"{verdict.detail} | {result.error_message}"
            if not try_replan(llm, state, trace, cfg, reason):
                break
            observation = f"(replanned after: {reason})"

        else:
            state.status = "needs_human"
            trace.log("escalate", reason=verdict.detail)
            break

    if state.status == "running":
        if state.step_index < len(state.plan):
            state.status = "failed"
        elif "invoice_no" in state.memory:
            report = verify_invoice(state, trace)
            state.status = "success" if report.passed else "partial"
        else:
            state.status = "partial"

    trace.report(state, tokens=llm.usage.total)
    return state
