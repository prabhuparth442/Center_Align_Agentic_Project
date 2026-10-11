"""The command line front end.

    python -m agent.cli                 stays open: asks for a goal, then for the next one
    python -m agent.cli "your goal"     one run, then exits
    -q                                  quiet: only questions and the final result

While a run goes you see the plan and one line per tool call. At the end you see the top of report.md.
When the agent needs a decision (ask_human, request_approval) it asks you here and waits for your answer.

Ctrl+C pauses after the current step:  r = resume, s = stop this run (the report is still written),
q = stop and quit the program. Ctrl+C a second time, or Ctrl+C at a question, stops right away.

It plugs in without editing loop.py: run_in_terminal() swaps in a Trace that also prints, an oracle that asks
you, and a next_action that checks for a pause. They are put back when the run ends.
"""
import signal
import sys
import traceback

from dotenv import load_dotenv

from . import loop
from .tools import browser, control
from .trace import Trace, short_args

DEFAULT_GOAL = ("Find the latest invoice from Company X Ltd in the inbox, "
                "enter it into the finance system, and confirm when it is done.")

# trace events that only need one line of text
SIMPLE_LINES = {
    "recover": "      recovery '{name}' (ok={ok})",
    "replan": "      replanned: {reason}",
    "escalate": "      stopping: {reason}",
    "budget": "      stopping: {reason}",
    "finish": "      finish: {status} - {summary}",
}


class StopRun(Exception):
    """Raised between two steps when the user chose to stop."""


class Front:
    def __init__(self, quiet=False):
        self.quiet = quiet
        self.state = None            # RunState and LLM, set when the loop starts planning
        self.llm = None
        self.trace = None
        self.last_action = None
        self.calls = 0
        self.waiting_for_input = False
        self.pause_requested = False
        self.stop_requested = False
        self.quit_program = False

    # --- output ---------------------------------------------------------------------

    def say(self, text):
        if not self.quiet:
            print(text, flush=True)    # flush so lines show up at once, also when piped

    def show(self, kind, data):
        """Called for every trace event."""
        if kind == "plan":
            self.say(f"Plan ({len(data['steps'])} steps):")
            for number, step in enumerate(data["steps"], 1):
                self.say(f"  {number}. {step['intent']}")

        elif kind == "policy":
            self.last_action = data["action"]
            if data["verdict"] != "allow":
                self.say(f"      policy says {data['verdict']}: {data['reason']}")

        elif kind == "check":
            self.calls = self.calls + 1
            args = ""
            # the check event has no arguments, take them from the policy event just before
            if self.last_action is not None and self.last_action["tool"] == data["tool"]:
                args = short_args(self.last_action["args"])
            result = data["verdict"]
            if data["error"]:
                result = f"{data['error']} -> {result}"
            self.say(f"  #{self.calls} {data['tool']} {args}  {result}")

        elif kind == "approval":
            if data["ok"] and data["data"]["approved"]:
                self.say("      approved by you")
            else:
                self.say("      approval refused, stopping")

        elif kind == "verify":
            if data["passed"]:
                self.say("      verifier: PASSED, the saved invoice matches")
            else:
                self.say(f"      verifier: FAILED {data['mismatches']}")

        elif kind in SIMPLE_LINES:
            self.say(SIMPLE_LINES[kind].format(**data))

    def show_final(self, state):
        """Always printed, also in quiet mode: the top of report.md (header and answer)."""
        report_file = self.trace.dir / "report.md"
        print("\n=== Result (top of report.md) ===")
        if report_file.exists():
            for line in report_file.read_text(encoding="utf-8").splitlines():
                if line.startswith("## Plan"):
                    break
                print(line)
        else:
            print(f"Run {state.run_id}: {state.status} (no report was written)")
        print("=================================")
        print(f"full report: {report_file}\n", flush=True)

    # --- questions ------------------------------------------------------------------

    def read_line(self, prompt):
        # While we wait for typing, Ctrl+C should interrupt right away (see on_interrupt)
        self.waiting_for_input = True
        try:
            return input(prompt)
        finally:
            self.waiting_for_input = False

    def ask(self, question, options=None):
        """The oracle for control.bind(): shows the question, waits for your answer, returns it."""
        print(f"\nThe agent needs a decision from you:\n  {question}")
        if options:
            print("  choose: " + " / ".join(str(option) for option in options))

        # the answer when we can't get a real one. Never let the agent assume a yes.
        safe_answer = ""
        if options and "no" in options:
            safe_answer = "no"

        while True:
            try:
                answer = self.read_line("> ").strip()
            except EOFError:
                print("(no input available)")    # input is piped or closed
                return safe_answer
            except KeyboardInterrupt:
                print("\n(Ctrl+C at the question: stopping the run)")
                self.stop_requested = True
                return safe_answer

            if answer == "":
                print("Please type an answer.")
                continue
            if not options:
                return answer

            # "y" is enough for "yes", as long as only one option starts with what was typed
            matches = [str(option) for option in options if str(option).lower().startswith(answer.lower())]
            if len(matches) == 1:
                return matches[0]
            print("Please choose one of: " + ", ".join(str(option) for option in options))

    # --- pause and stop ---------------------------------------------------------------

    def on_interrupt(self, signum, frame):
        """Ctrl+C. While the agent works it only asks for a pause, so a click is never cut in half."""
        if self.waiting_for_input or self.pause_requested:
            raise KeyboardInterrupt
        self.pause_requested = True
        print("\n(pause requested: pausing after the current step. Ctrl+C again to force quit)", flush=True)

    def check_pause(self):
        """Called between two steps. Waits in the pause menu, or raises StopRun if the user wants out."""
        if self.stop_requested:
            raise StopRun()
        if not self.pause_requested:
            return

        self.pause_requested = False
        print("\nPaused. The browser stays open and nothing is lost.")
        print("  r = resume    s = stop this run (the report is still written)    q = stop and quit")
        while True:
            try:
                answer = self.read_line("pause> ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                answer = "s"
            if answer == "r":
                print("Resuming ...", flush=True)
                return
            if answer == "q":
                self.quit_program = True
                raise StopRun()
            if answer == "s":
                raise StopRun()
            print("Please type r, s or q.")


class LiveTrace(Trace):
    """A Trace that also shows each event in the terminal."""

    def __init__(self, front, run_dir, run_id):
        super().__init__(run_dir, run_id)
        self.front = front
        front.trace = self

    def log(self, kind, **data):
        super().log(kind, **data)
        self.front.show(kind, data)


def end_early(front, status, reason):
    """The loop was cut off, so it never wrote its report. Do it here."""
    state = front.state
    state.status = status
    front.trace.log("escalate", reason=reason)
    front.trace.report(state, tokens=front.llm.usage.total)
    return state


def run_in_terminal(goal, quiet=False, front=None):
    if front is None:
        front = Front(quiet)

    # what we swap in for the length of the run, and put back at the end
    original_trace = loop.Trace
    original_bind = control.bind
    original_make_plan = loop.make_plan
    original_next_action = loop.next_action

    def make_trace(run_dir, run_id):
        trace = LiveTrace(front, run_dir, run_id)
        browser.set_download_dir(trace.dir / "downloads")    # each run keeps its own files
        return trace

    def bind_with_oracle(state, trace=None, oracle=None):
        original_bind(state, trace, front.ask)

    def planning(llm, state):
        front.state = state
        front.llm = llm
        front.say("Planning (asking the model) ...")    # the model calls are slow, show that we are alive
        return original_make_plan(llm, state)

    def choosing(llm, state, step, observation):
        front.check_pause()    # the loop calls this once per turn, so this is between two steps
        front.say(f"[{state.step_index + 1}/{len(state.plan)}] {step.intent[:90]}")
        return original_next_action(llm, state, step, observation)

    loop.Trace = make_trace
    control.bind = bind_with_oracle
    loop.make_plan = planning
    loop.next_action = choosing
    old_handler = signal.signal(signal.SIGINT, front.on_interrupt)
    try:
        print(f"Goal: {goal}")
        try:
            state = loop.run(goal)
        except StopRun:
            state = end_early(front, "needs_human", "stopped by you")
        except Exception as exc:
            # for example the model API is down or the key is wrong
            traceback.print_exc()
            if front.state is None:
                raise
            state = end_early(front, "failed", f"crashed: {type(exc).__name__}: {str(exc)[:150]}")
    finally:
        signal.signal(signal.SIGINT, old_handler)
        loop.Trace = original_trace
        control.bind = original_bind
        loop.make_plan = original_make_plan
        loop.next_action = original_next_action
        browser.close_session()    # also after a crash or force quit, so no Chromium is left running

    front.show_final(state)
    return state


def main():
    load_dotenv()
    words = [arg for arg in sys.argv[1:] if arg != "-q"]
    quiet = "-q" in sys.argv[1:]
    goal = " ".join(words)

    # a goal on the command line, or no terminal to type in: one run, then exit
    if goal or not sys.stdin.isatty():
        try:
            state = run_in_terminal(goal or DEFAULT_GOAL, quiet)
        except KeyboardInterrupt:
            print("\nstopped by user")
            sys.exit(130)
        if state.status == "success":
            sys.exit(0)
        sys.exit(1)

    # no goal given on a real terminal: stay open and take one goal after the other
    print("Agent ready. While it works, Ctrl+C pauses it. Type q to quit.")
    while True:
        try:
            answer = input("\nGoal (Enter = the default task, q = quit): ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if answer.lower() in ("q", "quit", "exit"):
            break

        front = Front(quiet)
        try:
            run_in_terminal(answer or DEFAULT_GOAL, quiet, front)
        except KeyboardInterrupt:
            print("\nrun force stopped")
        if front.quit_program:
            break
    print("bye")


if __name__ == "__main__":
    main()
