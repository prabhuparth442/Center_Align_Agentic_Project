"""Planner: goal -> short list of steps with expected outcomes. Thinking ON. Also replans on failure."""
from pydantic import BaseModel

from .llm import LLM
from .state import RunState
from .tools.base import describe_tools


class Step(BaseModel):
    intent: str      # what to do
    expect: str      # what should be true afterwards (checker uses this)


class Plan(BaseModel):
    steps: list[Step]


PLAN_PROMPT = """You plan office tasks done through a browser. Tools:\n{tools}\n
Return JSON {{"steps":[{{"intent":..., "expect":...}}]}}. Max 30 steps.
- Every step is exactly ONE tool call. Name the tool in the intent, e.g. "browser_open mail /inbox".
  Reading a page (browser_snapshot) is a step of its own.
- Never plan to guess values. Always read them from documents/pages first.
- Each 'expect' must be a concrete, verifiable postcondition (e.g. element visible, URL changed, fact in memory).
- The LAST step is always finish, with the real answer to the goal in its summary. Cover every part of the
  goal (for example both apps) before it.
- The step that saves or submits something must have the word "saved" in double quotes inside expect,
  because that is the word the finance app shows after a real save."""

REPLAN_PROMPT = """A browser automation plan failed mid-execution. Revise the plan to reach the original goal.

Tools:
{tools}

Original Goal: {goal}

Completed Steps:
{completed_steps}

Discovered Facts / Memory:
{memory_facts}

Failed Step:
- Intent: {failed_intent}
- Expected: {failed_expect}
- Failure Cause: {failure}

Rules:
1. DO NOT repeat already completed actions, especially irreversible writes/submissions.
2. Formulate alternative steps to bypass or resolve the failure cause.
3. Same step format: one tool call per step, tool named in the intent. Max 25 steps.
Return JSON {{"steps":[{{"intent":..., "expect":...}}]}} covering ONLY the remaining steps needed to finish."""


def make_plan(llm: LLM, state: RunState) -> Plan:
    msgs = [{"role": "system", "content": PLAN_PROMPT.format(tools=describe_tools())},
            {"role": "user", "content": state.goal}]
    return llm.structured(msgs, Plan, think=True)


def replan(llm: LLM, state: RunState, failure: str) -> Plan:
    """Include memory facts, steps done, and the failure; keep completed work, change the approach."""
    state.replans += 1
    completed = []
    failed_intent = "Unknown"
    failed_expect = "Unknown"

    for idx, step in enumerate(state.plan):
        if idx < state.step_index:
            completed.append(f"- Step {idx + 1}: {step.intent} (Succeeded)")
        elif idx == state.step_index:
            failed_intent = step.intent
            failed_expect = step.expect
            break

    completed_str = "\n".join(completed)
    if completed_str == "":
        completed_str = "None (failed on first step)"

    facts = []
    for fact in state.memory.values():
        facts.append(f"-{fact.key} : '{fact.value}' (evidence: {fact.evidence})")
    facts_str = "\n".join(facts)
    if facts_str == "":
        facts_str = "No facts recorded yet"

    prompt = REPLAN_PROMPT.format(
        tools = describe_tools(),
        goal = state.goal,
        completed_steps = completed_str,
        memory_facts = facts_str,
        failed_intent = failed_intent,
        failed_expect = failed_expect,
        failure = failure,
    )

    msgs = [
        {"role": "system", "content": prompt},
        {
            "role": "user",
            "content": (
                "Produce the revised plan for the remaining steps to fulfill the"
                " goal."
            ),
        },
    ]

    return llm.structured(msgs, Plan, think=True)
