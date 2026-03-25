#!/usr/bin/env python3
from __future__ import annotations

import json
import sys


def main() -> int:
    raw = json.loads(sys.stdin.read())
    task = raw["task"]
    must_contain = task.get("expected", {}).get("must_contain", [])
    final_output = " ".join(str(item) for item in must_contain)
    response = {
        "final_output": f"openclaw simulated result: {final_output}",
        "steps": 6,
        "tool_calls": 4,
        "tokens_in": 800,
        "tokens_out": 180,
        "cost_usd": 0.006,
        "trace": ["plan", "tool call", "tool call", "finalize"],
        "metadata": {"runner": "mock_openclaw_runner"},
    }
    sys.stdout.write(json.dumps(response))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
