"""Post a run's committed verdicts to Langfuse: `judge.grounded` and `judge.correct` per turn.

Run it from a machine whose .env has the PE_LANGFUSE_* settings (the tower):
    uv run python scripts/post_verdicts.py evals/runs/<run id>.verdicts.yaml
The scores go to the `eval` environment, as the run's traces did, each on its turn's trace with
the judge's note as comment. Posting again replaces them: a trace has one score per name.
"""

import argparse
import sys
from pathlib import Path

from paperexplorer.evals import eval_settings, load_verdicts, post_verdicts
from paperexplorer.tracing import configure_tracing


def main() -> int:
    """0 when every verdict was sent."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("verdicts", type=Path, help="evals/runs/<run id>.verdicts.yaml")
    args = parser.parse_args()
    verdicts = load_verdicts(args.verdicts)
    tracing = configure_tracing(eval_settings())
    if tracing.base_url is None:
        print("Set the PE_LANGFUSE_* keys in .env", file=sys.stderr)
        return 1
    posted = post_verdicts(tracing, verdicts)
    tracing.flush()
    print(f"Posted {posted} scores for run {verdicts.run_id} to {tracing.base_url} (eval)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
