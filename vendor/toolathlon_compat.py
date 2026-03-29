from __future__ import annotations

import sys
from pathlib import Path


def _add_toolathlon_repo_to_path() -> None:
    repo_root = Path(__file__).resolve().parent / "toolathlon"
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))


_add_toolathlon_repo_to_path()
