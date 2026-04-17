"""Warm container pool keyed by (repo, base_commit).

Phase 4 will wire this into the solve stage for 3-5× speedup. For Phase 1 we
keep a minimal stub so the public interface exists and higher layers can
import it without churn.
"""
from __future__ import annotations

from dataclasses import dataclass
from threading import Lock


@dataclass
class _PoolKey:
    repo_id: str
    base_commit: str

    def __hash__(self) -> int:
        return hash((self.repo_id, self.base_commit))


class WarmPool:
    """Placeholder; Phase 1 uses ephemeral-per-run evaluation."""

    def __init__(self, max_size: int = 16) -> None:
        self.max_size = max_size
        self._lock = Lock()
        self._containers: dict[_PoolKey, str] = {}

    def acquire(self, repo_id: str, base_commit: str) -> str | None:
        with self._lock:
            return self._containers.get(_PoolKey(repo_id, base_commit))

    def release(self, repo_id: str, base_commit: str, container_id: str) -> None:
        with self._lock:
            self._containers[_PoolKey(repo_id, base_commit)] = container_id

    def shutdown(self) -> None:
        """No-op in Phase 1."""
        self._containers.clear()
