from __future__ import annotations

import sys
from pathlib import Path


def _add_openhands_repo_to_path() -> None:
    repo_root = Path(__file__).resolve().parent / "openhands-benchmarks"
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))


_add_openhands_repo_to_path()

from benchmarks.utils.fake_user_response import (  # noqa: E402
    fake_user_response,
    run_conversation_with_fake_user_response,
)

__all__ = ["fake_user_response", "run_conversation_with_fake_user_response"]
