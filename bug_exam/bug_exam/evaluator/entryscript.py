"""Entryscript builder.

Direct adaptation of swe_bench_pro_eval.py:95-127. Same shape (env exports,
git reset, git apply, run_script, parser) but takes a typed ExamInstance
instead of a stringified CSV row.

The script runs *inside* the container. It assumes /workspace is mounted
read-write and contains patch.diff, run_script.sh, parser.py.
"""
from __future__ import annotations

import re
from pathlib import Path

from ..schema import ExamInstance


ENV_LINE = re.compile(r"^\s*ENV\s+(.+)$")


def _extract_env_exports(dockerfile_text: str) -> list[str]:
    """Pull ENV lines out of a Dockerfile and convert to bash exports."""
    exports: list[str] = []
    for line in dockerfile_text.splitlines():
        m = ENV_LINE.match(line)
        if m:
            exports.append("export " + m.group(1).strip())
    return exports


def build_entryscript(
    exam: ExamInstance,
    base_dockerfile_text: str,
    instance_dockerfile_text: str,
    patch_kind: str = "solver",
) -> str:
    """Produce the bash script that applies a candidate patch and runs tests.

    patch_kind:
      - "solver": apply the injection_patch first (buggy state), then apply
                  the solver's patch.diff on top (the solver's fix).
      - "baseline": apply nothing; tests must pass at HEAD.
      - "bug_only": apply only the injection_patch, to capture F2P/P2P.
    """
    env_cmds = _extract_env_exports(base_dockerfile_text)
    env_cmds += _extract_env_exports(instance_dockerfile_text)
    env_block = "\n".join(env_cmds)

    selected = ",".join(exam.selected_test_files) if exam.selected_test_files else ""
    before_cmd = (exam.before_repo_set_cmd or "").strip().split("\n")[-1] if exam.before_repo_set_cmd else ""

    # Apply sequence differs by patch_kind. We tolerate an empty bug_patch —
    # condition-A contamination runs use base_commit as the buggy state.
    if patch_kind == "solver":
        apply_block = (
            f"if [ -s /workspace/bug_patch.diff ]; then git apply -v /workspace/bug_patch.diff; fi\n"
            f"git apply -v /workspace/patch.diff || git apply --reject -v /workspace/patch.diff || true"
        )
    elif patch_kind == "bug_only":
        apply_block = "if [ -s /workspace/bug_patch.diff ]; then git apply -v /workspace/bug_patch.diff; fi"
    elif patch_kind == "baseline":
        apply_block = "# no patch — baseline test run"
    else:
        raise ValueError(f"unknown patch_kind {patch_kind!r}")

    script = f"""#!/bin/bash
set -uo pipefail
{env_block}
# repo state
cd /app
git reset --hard {exam.base_commit}
git checkout {exam.base_commit}
git clean -fd

# apply patches
{apply_block}

# before-repo-set (per-repo deps / services)
{before_cmd}

# run tests
bash /workspace/run_script.sh {selected} > /workspace/stdout.log 2> /workspace/stderr.log
_rc=$?

# parse
python /workspace/parser.py /workspace/stdout.log /workspace/stderr.log /workspace/output.json

exit $_rc
"""
    return script


def assemble_workspace(
    exam: ExamInstance,
    solver_patch: str,
    run_script_text: str,
    parser_text: str,
    base_dockerfile_text: str,
    instance_dockerfile_text: str,
    workspace_dir: Path,
    patch_kind: str = "solver",
) -> Path:
    """Write the files a container needs into workspace_dir. Returns the path."""
    workspace_dir.mkdir(parents=True, exist_ok=True)
    (workspace_dir / "bug_patch.diff").write_text(exam.injection_patch)
    (workspace_dir / "patch.diff").write_text(solver_patch or "")
    (workspace_dir / "run_script.sh").write_text(run_script_text)
    (workspace_dir / "parser.py").write_text(parser_text)
    entry = build_entryscript(exam, base_dockerfile_text, instance_dockerfile_text, patch_kind)
    entry_path = workspace_dir / "entryscript.sh"
    entry_path.write_text(entry)
    return entry_path
