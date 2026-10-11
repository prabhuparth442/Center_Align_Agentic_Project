"""Audit trace: one JSONL line per event + a markdown run report."""
import json
import re
import time
from pathlib import Path


def new_run_id(goal, run_dir=None):
    """A readable, sortable name for a run: 20261011-172610_full-summary-for-both-website"""
    stamp = time.strftime("%Y%m%d-%H%M%S")

    # the first words of the goal, as many as fit in 40 characters, so a name never ends in half a word
    slug = ""
    for word in re.findall("[a-z0-9]+", goal.lower()):
        if slug != "" and len(slug) + 1 + len(word) > 40:
            break
        if slug == "":
            slug = word[:40]
        else:
            slug = slug + "-" + word
    if slug == "":
        slug = "run"

    run_id = f"{stamp}_{slug}"
    # two runs with the same goal in the same second would share a folder
    if run_dir is not None:
        first_try = run_id
        counter = 2
        while (Path(run_dir) / run_id).exists():
            run_id = f"{first_try}-{counter}"
            counter = counter + 1
    return run_id


def short_args(args):
    """{'app': 'mail', 'path': '/inbox'} -> 'app=mail path=/inbox', long values cut."""
    parts = []
    for key, value in args.items():
        text = str(value)
        if len(text) > 40:
            text = text[:37] + "..."
        parts.append(f"{key}={text}")
    line = " ".join(parts)
    if len(line) > 90:
        line = line[:87] + "..."
    return line


class Trace:
    def __init__(self, run_dir, run_id):
        self.dir = Path(run_dir) / run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "trace.jsonl"

    def log(self, kind, **data):
        """kind: plan|action|policy|observation|check|verify|finish"""
        line = json.dumps({"t": time.time(), "kind": kind, **data}, default=str)
        # utf-8 on purpose, a rupee sign in a quote would break the default encoding on some machines
        with self.path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def events(self, kind=None):
        """Read the trace back. With a kind, only the events of that kind."""
        if not self.path.exists():
            return []
        events = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip() == "":
                continue
            event = json.loads(line)
            if kind is None or event["kind"] == kind:
                events.append(event)
        return events

    def stop_reason(self):
        """Why the run ended without success, taken from the trace. Empty if the trace doesn't say."""
        for event in reversed(self.events()):
            if event["kind"] in ("escalate", "budget"):
                return event["reason"]
            if event["kind"] == "finish" and event["status"] != "success":
                return f"the agent ended the run as {event['status']}"
            if event["kind"] == "approval":
                answer = event.get("data")
                if not event.get("ok") or not answer or not answer.get("approved"):
                    return f"approval not given ({event.get('reason')})"
        return ""

    def answer_lines(self, state):
        """What the user reads first. It is an answer only if the goal was reached."""
        done = state.status == "success"
        lines = []

        if state.answer != "":
            if not done:
                # the summary of a run that gave up is the agent's last message, not an answer
                lines.append("The agent stopped before finishing, and said:")
            lines.append(state.answer)
            if len(state.answer_evidence) > 0:
                lines += ["", "Evidence:"]
                for item in state.answer_evidence:
                    lines.append(f"- {item}")
        elif done and state.verified:
            lines.append("The invoice was entered and the read-back matches the document.")
        elif done:
            lines.append("The run ended as success but the agent wrote no answer.")
        else:
            lines.append("The goal was not completed, and the agent wrote no final answer.")

        # a run without a written answer, or one that gave up: show what it had found
        if len(state.memory) > 0 and (state.answer == "" or not done):
            lines += ["", "What the agent found so far:"]
            for fact in state.memory.values():
                value = fact.value
                if len(value) > 150:
                    value = value[:147] + "..."
                lines.append(f"- {fact.key} = {value}")
        return lines

    def report(self, state, tokens=None):
        """Write report.md. The top (header and answer) is what the terminal shows at the end of a run."""
        if state.verified:
            verified_text = "yes"
        else:
            verified_text = "no"
        lines = [f"# Run {state.run_id}", "",
                 f"Goal: {state.goal}", f"Status: {state.status}",
                 f"Verified by read-back: {verified_text}"]
        lines.append(f"Tool calls: {state.steps_used}, replans: {state.replans}")
        if tokens is not None:
            lines.append(f"Tokens used: {tokens}")
        reason = self.stop_reason()
        if reason != "" and state.status != "success":
            lines.append(f"Stopped because: {reason}")

        if state.status == "success":
            lines += ["", "## Answer"]
        else:
            lines += ["", "## Not completed"]
        lines += self.answer_lines(state)

        # the plan as the planner first made it (a replan changes state.plan afterwards)
        lines += ["", "## Plan"]
        plan_events = self.events("plan")
        if len(plan_events) > 0:
            steps = plan_events[0]["steps"]
        else:
            steps = [step.model_dump() for step in state.plan]
        number = 1
        for step in steps:
            lines.append(f"{number}. {step['intent']}")
            number = number + 1

        lines += ["", "## What the agent did"]
        last_action = None
        for event in self.events():
            kind = event["kind"]
            if kind == "policy":
                last_action = event["action"]
                if event["verdict"] != "allow":
                    action_text = f"{last_action.get('tool', '?')} {short_args(last_action.get('args', {}))}"
                    lines.append(f"- policy {event['verdict']} for {action_text}: {event['reason']}")
            elif kind == "check":
                args_text = ""
                if last_action is not None and last_action.get("tool") == event["tool"]:
                    args_text = " " + short_args(last_action.get("args", {}))
                result = event["verdict"]
                if event["error"]:
                    result = f"{event['error']} -> {event['verdict']}"
                lines.append(f"- {event['tool']}{args_text}: {result}")
            elif kind == "recover":
                lines.append(f"  recovery '{event['name']}'")
            elif kind == "replan":
                lines.append(f"- replanned: {event['reason'][:120]}")
            elif kind in ("escalate", "budget"):
                lines.append(f"- stopped: {event['reason']}")

        lines += ["", "## Facts the agent saved"]
        if len(state.memory) == 0:
            lines.append("(none)")
        for fact in state.memory.values():
            lines.append(f"- {fact.key} = {fact.value}  ({fact.evidence})")

        lines += ["", "## Verifier"]
        verifications = self.events("verify")
        if len(verifications) == 0:
            lines.append("The verifier did not run.")
        else:
            last = verifications[-1]
            if last["passed"]:
                lines.append("PASSED")
            else:
                lines.append("FAILED")
            for mismatch in last.get("mismatches", []):
                lines.append(f"- {mismatch}")

        report_path = self.dir / "report.md"
        report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return report_path
