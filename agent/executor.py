"""Executor: one step + latest observation -> exactly one tool call as JSON. Thinking OFF."""
from pydantic import BaseModel

# The tools register themselves when their module is imported. Without this the registry would be
# empty and the model would be shown no tools at all.
from . import tools  # noqa: F401
from .tools.base import REGISTRY, describe_tools

MAX_OBSERVATION_CHARS = 3000   # TOOLS.md: keep observations compact
EXTRA_TRIES = 2                # how often a bad tool name / argument goes back to the model

PROMPT = """You carry out ONE step of a browser task by choosing ONE tool.

Tools:
{tools}

Reply with JSON only: {{"tool": "<name>", "args": {{...}}}}
Rules:
- Use only values you can see in the observation or the known facts. Never invent values.
- Do not put passwords or secrets in args. The login tools use stored credentials.
- If the step is already done according to the observation, pick the tool that checks or moves on."""


class Action(BaseModel):
    tool: str
    args: dict = {}


def trim(text, limit=MAX_OBSERVATION_CHARS):
    """Cut long observations. Keeps the start, the page summary and elements come first."""
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[trimmed]"


def build_messages(state, step, observation):
    # the facts are small, so the model gets all of them (value only, not the evidence)
    fact_lines = []
    for fact in state.memory.values():
        fact_lines.append(f"- {fact.key} = {fact.value}")
    facts = "\n".join(fact_lines)
    if facts == "":
        facts = "(none yet)"

    user = (
        f"Goal: {state.goal}\n\n"
        f"Current step: {step.intent}\n"
        f"Expected afterwards: {step.expect}\n\n"
        f"Known facts:\n{facts}\n\n"
        f"Last observation:\n{trim(observation)}"
    )
    return [{"role": "system", "content": PROMPT.format(tools=describe_tools())},
            {"role": "user", "content": user}]


def problem_with(action):
    """Pydantic only checks the shape. This checks that the tool and argument names exist."""
    tool = REGISTRY.get(action.tool)
    if tool is None:
        return f"Unknown tool '{action.tool}'. Choose one of: {', '.join(REGISTRY)}"

    unknown_args = []
    for name in action.args:
        if name not in tool.args:
            unknown_args.append(name)
    if len(unknown_args) > 0:
        return f"Tool '{action.tool}' has no argument(s) {unknown_args}. It takes: {list(tool.args)}"
    return None


def next_action(llm, state, step, observation):
    """Ask the model for one tool call. Bad JSON is repaired inside llm.structured;
    an unknown tool or argument name is sent back here, up to EXTRA_TRIES times."""
    messages = build_messages(state, step, observation)
    problem = None
    for attempt in range(EXTRA_TRIES + 1):
        action = llm.structured(messages, Action, think=False)   # thinking off: fast and cheap
        problem = problem_with(action)
        if problem is None:
            return action
        messages.append({"role": "assistant", "content": action.model_dump_json()})
        messages.append({"role": "user", "content": f"{problem}. Reply with JSON only."})
    # The loop decides what to do (replan or needs_human). We never run a tool we can't identify.
    raise ValueError(f"executor could not produce a valid tool call: {problem}")
