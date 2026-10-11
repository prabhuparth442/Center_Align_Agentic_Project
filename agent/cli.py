"""python -m agent.cli "<goal>" """
import sys

from dotenv import load_dotenv

from .loop import run

DEFAULT_GOAL = ("Find the latest invoice from Company X Ltd in the inbox, "
                "enter it into the finance system, and confirm when it is done.")

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
