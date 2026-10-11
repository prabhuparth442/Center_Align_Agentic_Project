"""Terminal front end: shows what the agent is doing while it runs, asks you when it needs a person,
and lets you pause or stop a run without closing the program.

Run it:
    python -m agent.terminal_front                    stays open, asks for a goal, then for the next one
    python -m agent.terminal_front "your goal"        one run, then exits (good for scripts)
    add -q for quiet: only questions and the end result

What you get:
    - a line when the agent starts planning and when it thinks about a step  [4/13] ...
    - one line per tool call with its result and what the checker decided     #7 browser_click ... -> ok
    - policy blocks, recoveries, replans and the reason a run stopped
    - a prompt in the terminal when the agent calls ask_human or request_approval, and it waits for you
    - a summary at the end with the report path

Pause and stop (Ctrl+C while the agent is working):
    First Ctrl+C   asks for a pause. The agent finishes the step it is on (never stops in the middle of
                   a click or a model call), then shows a menu:
                       r  resume       s  stop this run, write the report, keep the program open
                       q  stop this run and quit the program
    Second Ctrl+C  before it paused: force quit right now (the browser is still closed properly).
    Ctrl+C at a question the agent asked you: stops the run (the answer counts as "no").
    A stopped run ends as needs_human with the reason "stopped by you", and report.md is still written.

How it hooks in without touching loop.py:
    loop.py builds its own Trace and calls control.bind(state, trace) with no oracle (nobody to ask).
    Every event the loop cares about already goes through trace.log(), so LiveTrace below is a Trace
    that also prints. run_in_terminal() swaps in LiveTrace and a terminal oracle while the run is going,
    then puts the originals back. The pause check sits in front of the executor call, which the loop makes
    once at the top of every turn: a safe place between two steps.
    The cleaner way later: let loop.run() take `trace` and `oracle` arguments and check a stop flag.
    Then the swapping goes away.
"""
import os
import signal
import sys

from dotenv import load_dotenv

from . import loop
from .cli import DEFAULT_GOAL
from .tools import browser, control
from .trace import Trace

COLOR_CODES = {"green": "32", "red": "31", "yellow": "33", "grey": "90", "bold": "1"}


class StopRun(Exception):
    """Raised at a safe point when the user chose to stop the run."""


class TerminalFront:
    def __init__(self, quiet=False):
        self.quiet = quiet
        # colors only on a real terminal, and NO_COLOR is the usual way to turn them off
        self.use_color = sys.stdout.isatty() and "NO_COLOR" not in os.environ
        self.call_number = 0
        self.last_action = None
        self.stop_reason = ""
        self.report_dir = None
        self.state = None            # the RunState, set when the loop first asks for an action
        self.trace = None            # the LiveTrace of this run
        self.pause_requested = False
        self.stop_requested = False
        self.quit_program = False

    # --- output ---------------------------------------------------------------------

    def paint(self, text, color):
        if not self.use_color:
            return text
        return f"\033[{COLOR_CODES[color]}m{text}\033[0m"

    def say(self, text):
        if self.quiet:
            return
        # flush so the line shows up right away, also when the output is piped to a file
        print(text, flush=True)

    def short_args(self, args):
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

    def show_event(self, kind, data):
        """Called for every trace event. Prints the ones worth showing."""
        if kind == "plan":
            self.say(self.paint(f"Plan ({len(data['steps'])} steps):", "bold"))
            number = 1
            for step in data["steps"]:
                self.say(f"  {number}. {step['intent']}")
                number = number + 1

        elif kind == "policy":
            self.last_action = data["action"]
            if data["verdict"] != "allow":
                self.say(self.paint(f"      policy says {data['verdict']}: {data['reason']}", "yellow"))

        elif kind == "check":
            self.show_tool_call(data)

        elif kind == "recover":
            if data["ok"]:
                outcome = "worked"
            else:
                outcome = "failed"
            self.say(self.paint(f"      recovery '{data['name']}' {outcome}", "yellow"))

        elif kind == "replan":
            self.say(self.paint(f"      replanned, new plan has {len(data['steps'])} steps. Reason: {data['reason'][:100]}", "yellow"))

        elif kind == "approval":
            # the loop logs this after the question was answered. data is None when nobody could be asked
            if data["ok"] and data["data"]["approved"]:
                self.say(self.paint("      approved by you", "green"))
            else:
                self.stop_reason = f"approval not given ({data['reason']})"
                self.say(self.paint("      approval refused, stopping", "red"))

        elif kind in ("escalate", "budget"):
            self.stop_reason = data["reason"]
            self.say(self.paint(f"      stopping: {self.stop_reason}", "red"))

        elif kind == "verify":
            if data["passed"]:
                self.say(self.paint("      verifier: PASSED, the saved invoice matches", "green"))
            else:
                self.say(self.paint(f"      verifier: FAILED {data['mismatches']}", "red"))

        elif kind == "finish":
            self.say(f"      finish: {data['status']} - {data['summary']}")

    def show_tool_call(self, data):
        self.call_number = self.call_number + 1
        args_text = ""
        # the check event doesn't carry the arguments, so use the ones from the policy event just before
        if self.last_action is not None and self.last_action["tool"] == data["tool"]:
            args_text = self.short_args(self.last_action["args"])

        result = data["verdict"]
        if data["error"]:
            result = f"{data['error']} -> {data['verdict']}"

        if data["verdict"] == "ok":
            color = "green"
        elif data["verdict"] == "escalate":
            color = "red"
        else:
            color = "yellow"
        self.say(f"  #{self.call_number} {data['tool']} {args_text}  " + self.paint(result, color))

    def show_final(self, state):
        # the summary is always printed, also in quiet mode
        print()
        print(self.paint(f"=== Run {state.run_id}: {state.status} ===", "bold"))
        if state.verified:
            print("  verified by read-back: yes")
        else:
            print("  verified by read-back: no")
        print(f"  tool calls: {state.steps_used}, replans: {state.replans}")
        if self.stop_reason:
            print(f"  why it stopped: {self.stop_reason}")
        if len(state.memory) > 0:
            print("  facts remembered:")
            for fact in state.memory.values():
                print(f"    {fact.key} = {fact.value}")
        if self.report_dir is not None:
            print(f"  report: {self.report_dir / 'report.md'}")
        print(flush=True)

    # --- input ----------------------------------------------------------------------

    def read_line(self, prompt):
        """input(), but Ctrl+C raises KeyboardInterrupt here. While the agent works, Ctrl+C only asks for a pause."""
        old_handler = signal.signal(signal.SIGINT, signal.default_int_handler)
        try:
            return input(prompt)
        finally:
            signal.signal(signal.SIGINT, old_handler)

    def ask(self, question, options=None):
        """The oracle for control.bind(): shows the question, waits until you answer, returns the answer."""
        print()
        print(self.paint("The agent needs a decision from you:", "bold"))
        print(f"  {question}")
        if options:
            print("  choose: " + " / ".join(str(option) for option in options))

        # the safe answer when we can't get a real one. Never let the agent assume a yes.
        safe_answer = ""
        if options and "no" in options:
            safe_answer = "no"

        while True:
            try:
                answer = self.read_line("> ").strip()
            except EOFError:
                # no keyboard to read from (input is piped or closed)
                print("(no input available)")
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

            # accept "y" for "yes", as long as only one option starts with what was typed
            matches = []
            for option in options:
                if str(option).lower().startswith(answer.lower()):
                    matches.append(str(option))
            if len(matches) == 1:
                return matches[0]
            print("Please choose one of: " + ", ".join(str(option) for option in options))

    # --- pause and stop ---------------------------------------------------------------

    def on_interrupt(self, signum, frame):
        """Ctrl+C while the agent is working."""
        if self.pause_requested:
            # second Ctrl+C before it got to pause: the user wants out now
            raise KeyboardInterrupt
        self.pause_requested = True
        print("\n(pause requested: the agent pauses after the step it is on. Press Ctrl+C again to force quit)", flush=True)

    def check_for_pause_or_stop(self):
        """Called between two steps. Waits in the pause menu if asked, raises StopRun if the user wants to stop."""
        if self.stop_requested:
            raise StopRun()
        if not self.pause_requested:
            return

        self.pause_requested = False
        choice = self.pause_menu()
        if choice == "resume":
            print("Resuming ...", flush=True)
            return
        if choice == "quit":
            self.quit_program = True
        raise StopRun()

    def pause_menu(self):
        print()
        print(self.paint("Paused. The browser is still open and nothing is lost.", "bold"))
        print("  r = resume    s = stop this run (the report is still written)    q = stop and quit the program")
        while True:
            try:
                answer = self.read_line("pause> ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                return "stop"
            if answer in ("r", "resume"):
                return "resume"
            if answer in ("s", "stop"):
                return "stop"
            if answer in ("q", "quit"):
                return "quit"
            print("Please type r, s or q.")


class LiveTrace(Trace):
    """A Trace that also shows each event in the terminal."""

    def __init__(self, front, run_dir, run_id):
        super().__init__(run_dir, run_id)
        self.front = front
        front.report_dir = self.dir
        front.trace = self

    def log(self, kind, **data):
        super().log(kind, **data)
        self.front.show_event(kind, data)


def run_in_terminal(goal, quiet=False, front=None):
    if front is None:
        front = TerminalFront(quiet)

    # things we swap in for the length of the run, and put back at the end
    original_trace = loop.Trace
    original_bind = control.bind
    original_make_plan = loop.make_plan
    original_next_action = loop.next_action
    original_replan = loop.replan

    def make_trace(run_dir, run_id):
        return LiveTrace(front, run_dir, run_id)

    def bind_with_terminal(state, trace=None, oracle=None):
        original_bind(state, trace, front.ask)

    # The model calls are the slow part. Without these lines the terminal looks frozen.
    def planning(llm, state):
        front.say(front.paint("Planning (asking the model) ...", "grey"))
        return original_make_plan(llm, state)

    def choosing(llm, state, step, observation):
        front.state = state
        front.check_for_pause_or_stop()    # the loop calls this once per turn, between two steps
        front.say(front.paint(f"[{state.step_index + 1}/{len(state.plan)}] {step.intent[:90]}", "grey"))
        return original_next_action(llm, state, step, observation)

    def replanning(llm, state, reason):
        front.say(front.paint("Replanning (asking the model) ...", "grey"))
        return original_replan(llm, state, reason)

    loop.Trace = make_trace
    control.bind = bind_with_terminal
    loop.make_plan = planning
    loop.next_action = choosing
    loop.replan = replanning
    old_handler = signal.signal(signal.SIGINT, front.on_interrupt)
    try:
        print(front.paint(f"Goal: {goal}", "bold"))
        try:
            state = loop.run(goal)
        except StopRun:
            # The loop was cut off at a safe point, so it never wrote its report. Do that here.
            state = front.state
            state.status = "needs_human"
            front.trace.log("escalate", reason="stopped by you")
            front.trace.report(state)
    finally:
        signal.signal(signal.SIGINT, old_handler)
        loop.Trace = original_trace
        control.bind = original_bind
        loop.make_plan = original_make_plan
        loop.next_action = original_next_action
        loop.replan = original_replan
        browser.close_session()    # also after a crash or force quit, so no Chromium is left running

    front.show_final(state)
    return state


def main():
    load_dotenv()
    args = sys.argv[1:]
    quiet = "-q" in args
    words = []
    for arg in args:
        if arg != "-q":
            words.append(arg)
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
            print()
            break
        if answer.lower() in ("q", "quit", "exit"):
            break

        front = TerminalFront(quiet)
        try:
            run_in_terminal(answer or DEFAULT_GOAL, quiet, front)
        except KeyboardInterrupt:
            print("\nrun force stopped")
        if front.quit_program:
            break
    print("bye")


if __name__ == "__main__":
    main()
