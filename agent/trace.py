"""Audit trace: one JSONL line per event + a markdown run report."""
import json
import time
from pathlib import Path


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

    def report(self, state, tokens=None):
        """Write report.md: goal, plan, steps, evidence, verifier result, tokens, status."""
        if state.verified:
            verified_text = "yes"
        else:
            verified_text = "no"
        lines = [f"# Run {state.run_id}", "",
                 f"Goal: {state.goal}", f"Status: {state.status}",
                 f"Verified by read-back: {verified_text}"]
        if tokens is not None:
            lines.append(f"Tokens used: {tokens}")

        lines += ["", "## Plan"]
        number = 1
        for step in state.plan:
            # the plan holds Step objects, but a plain string works too
            if isinstance(step, str):
                text = step
            else:
                text = step.intent
            lines.append(f"{number}. {text}")
            number = number + 1

        lines += ["", "## Steps"]
        for event in self.events("check"):
            lines.append(f"- {event.get('tool')}: ok={event.get('ok')} error={event.get('error')} -> {event.get('verdict')}")
        for event in self.events("policy"):
            if event.get("verdict") != "allow":
                lines.append(f"- policy {event['verdict']}: {event.get('reason')}")

        lines += ["", "## Evidence"]
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
