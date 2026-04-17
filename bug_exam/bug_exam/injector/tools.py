"""Tool implementations exposed to the injector LLM.

The tools operate on a repo checkout on disk (not inside Docker). They're
read-only plus one tool that runs the baseline test suite in the envbuild
container. The injector uses them to explore the code, pick anchors, and
sanity-check plausibility before emitting a break plan.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class RepoTools:
    repo_dir: Path
    image_tag: str | None = None

    # --- read_file ---------------------------------------------------------
    def read_file(self, rel_path: str, start: int = 1, end: int | None = None) -> str:
        p = self.repo_dir / rel_path
        if not p.exists() or not p.is_file():
            return f"error: {rel_path} not found"
        try:
            text = p.read_text(errors="replace")
        except Exception as e:
            return f"error reading {rel_path}: {e}"
        lines = text.splitlines()
        s = max(1, start)
        e = end if end is not None else len(lines)
        e = min(e, len(lines))
        numbered = [f"{i:6d}  {lines[i-1]}" for i in range(s, e + 1)]
        return "\n".join(numbered)

    # --- list_dir ----------------------------------------------------------
    def list_dir(self, rel_path: str = ".") -> str:
        p = self.repo_dir / rel_path
        if not p.exists() or not p.is_dir():
            return f"error: {rel_path} is not a directory"
        entries = []
        for child in sorted(p.iterdir()):
            if child.name.startswith("."):
                continue
            suffix = "/" if child.is_dir() else ""
            entries.append(child.name + suffix)
        return "\n".join(entries)

    # --- grep --------------------------------------------------------------
    def grep(self, pattern: str, glob: str = "**/*.py", max_results: int = 100) -> str:
        import re
        rx = re.compile(pattern)
        hits: list[str] = []
        for path in self.repo_dir.glob(glob):
            if not path.is_file():
                continue
            try:
                for i, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
                    if rx.search(line):
                        rel = path.relative_to(self.repo_dir)
                        hits.append(f"{rel}:{i}: {line.strip()[:200]}")
                        if len(hits) >= max_results:
                            return "\n".join(hits)
            except Exception:
                continue
        return "\n".join(hits) if hits else "no matches"

    # --- list_tests --------------------------------------------------------
    def list_tests(self) -> str:
        """Return a newline-separated list of pytest test files in the repo."""
        tests: list[str] = []
        for pattern in ("test_*.py", "*_test.py"):
            for p in self.repo_dir.rglob(pattern):
                rel = p.relative_to(self.repo_dir)
                if any(part.startswith(".") for part in rel.parts):
                    continue
                tests.append(str(rel))
        return "\n".join(sorted(set(tests)))

    # --- run_tests (docker) -----------------------------------------------
    def run_tests(self, test_files: str = "", timeout_s: int = 600) -> str:
        """Run the baseline test suite inside the repo's image.

        The injector uses this sparingly (it's slow) to confirm baseline
        stability or to see which tests are likely to be affected by a plan.
        """
        if not self.image_tag:
            return "error: no image_tag configured"
        cmd = ["docker", "run", "--rm", "--platform", "linux/amd64",
               "-v", f"{self.repo_dir}:/app:ro", self.image_tag,
               "bash", "-lc",
               f"cd /app && python -m pytest --tb=short -q {test_files or ''}"]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
            return (res.stdout + "\n" + res.stderr)[-8000:]
        except subprocess.TimeoutExpired:
            return "error: test run timed out"
