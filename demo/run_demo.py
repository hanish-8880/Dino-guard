"""60-second scripted risky session that shows every runner state. Same as `dinoguard demo`.

    python demo/run_demo.py            # animated terminal runner
    python demo/run_demo.py --seconds 30

Runs against a throwaway DINOGUARD_HOME, so your real data is never touched.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dinoguard.cli.main import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(["demo", *sys.argv[1:]]))
