"""Plain one-shot runner: python -m agent.test_cli "<goal>"

No live output, no questions, no pause. It runs the loop once and prints one line at the end.
Good for scripts and quick checks. For hands-on use run `python -m agent.cli` instead (live output,
questions in the terminal, pause and stop).
"""
import sys

from dotenv import load_dotenv

from .cli import DEFAULT_GOAL
from .loop import run

if __name__ == "__main__":
    load_dotenv()
    try:
        state = run(" ".join(sys.argv[1:]) or DEFAULT_GOAL)
    except KeyboardInterrupt:
        print("stopped by user")
        sys.exit(130)
    print(f"run {state.run_id}: {state.status}")
    # non-zero exit code on anything but success, so a script can tell
    if state.status == "success":
        sys.exit(0)
    sys.exit(1)
