"""Per-framework test-output parsers.

Each parser is a self-contained script that runs *inside* the container. It
reads stdout.log + stderr.log and writes output.json with the canonical shape:

    {"tests": [{"name": "...", "status": "PASSED|FAILED|SKIPPED|ERROR"}, ...]}

Framework selection happens at envbuild time; the bytes of the selected
parser are materialized into data/run_scripts/<instance_id>/parser.py.
"""
from pathlib import Path


PARSERS_DIR = Path(__file__).parent


def load_parser_text(framework: str) -> str:
    """Load the parser script bytes for a given framework name."""
    path = PARSERS_DIR / f"{framework}.py"
    if not path.exists():
        raise FileNotFoundError(f"No parser for framework {framework!r} at {path}")
    return path.read_text()
