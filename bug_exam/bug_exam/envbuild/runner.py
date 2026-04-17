"""Envbuild runner.

Given a RepoManifest + a detected TestFrameworkSpec, this module:
  1. Renders a per-instance Dockerfile from a Jinja template
  2. Renders a run_script.sh + copies the appropriate parser.py
  3. Writes all three to data/dockerfiles/base/<instance_id>/ and
     data/run_scripts/<instance_id>/
  4. Builds the docker image
  5. Runs the baseline test suite three times and returns the stable
     passing-test set

Phase 1 is Python-only but the scaffolding is language-agnostic.
"""
from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..evaluator.parsers import load_parser_text
from ..schema import RepoManifest
from .detector import TestFrameworkSpec

log = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"


@dataclass
class BaselineResult:
    passing_tests: set[str]        # deterministic passing set (∩ over 3 runs)
    flaky_tests: set[str]          # differed between runs
    all_tests_seen: set[str]
    stable: bool
    raw_runs: list[list[dict]]     # raw parser outputs per run


def _instance_id(repo: RepoManifest) -> str:
    return f"bexam__{repo.id}__{repo.base_commit[:12]}"


def _dockerhub_safe_tag(repo: RepoManifest) -> str:
    return f"bug-exam/{repo.language.value}.{repo.owner.lower()}.{repo.name.lower()}:{repo.base_commit[:12]}"


def _default_platform() -> str | None:
    if platform.machine() in ("arm64", "aarch64"):
        return "linux/amd64"
    return None


class EnvBuilder:
    def __init__(self, data_root: Path):
        self.data_root = Path(data_root)
        self.dockerfiles_base_root = self.data_root / "dockerfiles" / "base"
        self.run_scripts_root = self.data_root / "run_scripts"
        self.jinja = Environment(
            loader=FileSystemLoader(str(TEMPLATES_DIR)),
            autoescape=select_autoescape([]),
            keep_trailing_newline=True,
        )

    # --- rendering --------------------------------------------------------

    def render_dockerfile(self, repo: RepoManifest, spec: TestFrameworkSpec) -> str:
        tpl_name = f"{spec.language}.Dockerfile.j2"
        tpl = self.jinja.get_template(tpl_name)
        return tpl.render(
            base_image=spec.base_image,
            system_deps=spec.system_deps,
            repo_url=repo.url,
            base_commit=repo.base_commit,
            install_cmd=spec.install_cmd,
            deps_cmd=spec.deps_cmd,
        )

    def render_run_script(self, spec: TestFrameworkSpec) -> str:
        tpl_name = f"{spec.language}.run_script.sh.j2"
        tpl = self.jinja.get_template(tpl_name)
        return tpl.render(
            install_cmd=spec.install_cmd,
            deps_cmd=spec.deps_cmd,
            test_cmd=spec.test_cmd,
        )

    def materialize(
        self,
        repo: RepoManifest,
        spec: TestFrameworkSpec,
    ) -> tuple[Path, Path, Path, Path, str]:
        """Write dockerfile + run_script + parser + instance_info to disk.

        Returns (dockerfile_path, run_script_path, parser_path, info_path, instance_id).
        """
        iid = _instance_id(repo)
        df_dir = self.dockerfiles_base_root / iid
        df_dir.mkdir(parents=True, exist_ok=True)
        df_path = df_dir / "Dockerfile"
        df_path.write_text(self.render_dockerfile(repo, spec))

        rs_dir = self.run_scripts_root / iid
        rs_dir.mkdir(parents=True, exist_ok=True)
        rs_path = rs_dir / "run_script.sh"
        rs_path.write_text(self.render_run_script(spec))
        rs_path.chmod(0o755)

        parser_path = rs_dir / "parser.py"
        parser_path.write_text(load_parser_text(spec.parser))

        info = {
            "instance_id": iid,
            "repo": f"{repo.owner}/{repo.name}",
            "language": repo.language.value,
            "base_commit": repo.base_commit,
            "test_framework": spec.name,
            "parser": spec.parser,
        }
        info_path = rs_dir / "instance_info.json"
        info_path.write_text(json.dumps(info, indent=2))

        return df_path, rs_path, parser_path, info_path, iid

    # --- docker build + baseline -----------------------------------------

    def build_image(self, dockerfile_path: Path, tag: str) -> bool:
        plat = _default_platform()
        cmd = ["docker", "build", "-t", tag, "-f", str(dockerfile_path)]
        if plat:
            cmd += ["--platform", plat]
        cmd.append(str(dockerfile_path.parent))
        log.info("docker build: %s", " ".join(cmd))
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if res.returncode != 0:
            log.warning("docker build failed for %s:\n%s", tag, res.stderr[-2000:])
            return False
        return True

    def run_baseline(
        self,
        image_tag: str,
        run_script_path: Path,
        parser_path: Path,
        runs_dir: Path,
        n_runs: int = 3,
        timeout_s: int = 900,
    ) -> BaselineResult:
        """Execute the baseline test suite n_runs times; compute stable set."""
        try:
            import docker
        except ImportError as e:
            raise RuntimeError("docker SDK not installed") from e
        client = docker.from_env()

        raw_runs: list[list[dict]] = []
        for i in range(n_runs):
            wdir = runs_dir / f"baseline_{i}"
            wdir.mkdir(parents=True, exist_ok=True)
            shutil.copy(run_script_path, wdir / "run_script.sh")
            shutil.copy(parser_path, wdir / "parser.py")
            (wdir / "entryscript.sh").write_text(
                "#!/bin/bash\nset -uo pipefail\ncd /app\n"
                "bash /workspace/run_script.sh > /workspace/stdout.log 2> /workspace/stderr.log || true\n"
                "python /workspace/parser.py /workspace/stdout.log /workspace/stderr.log /workspace/output.json\n"
            )
            abs_w = os.path.abspath(wdir)
            plat = _default_platform()
            kwargs = {
                "volumes": {abs_w: {"bind": "/workspace", "mode": "rw"}},
                "detach": True,
                "entrypoint": "/bin/bash",
                "command": ["-c", "bash /workspace/entryscript.sh"],
                "working_dir": "/app",
                "mem_limit": "8g",
                "nano_cpus": 4 * 10**9,
            }
            if plat:
                kwargs["platform"] = plat
            container = client.containers.run(image_tag, **kwargs)
            try:
                container.wait(timeout=timeout_s)
            except Exception as e:
                log.warning("baseline run %d timed out: %r", i, e)
                try:
                    container.kill()
                except Exception:
                    pass
            finally:
                try:
                    container.remove(force=True)
                except Exception:
                    pass
            out_path = wdir / "output.json"
            if out_path.exists():
                try:
                    raw_runs.append(json.loads(out_path.read_text()).get("tests", []))
                except Exception:
                    raw_runs.append([])
            else:
                raw_runs.append([])

        # Compute stable passing set
        all_seen: set[str] = set()
        per_run_passed: list[set[str]] = []
        for tests in raw_runs:
            passed = {t["name"] for t in tests if t.get("status") == "PASSED"}
            per_run_passed.append(passed)
            all_seen.update(t["name"] for t in tests)

        if per_run_passed:
            stable_passing = set.intersection(*per_run_passed)
        else:
            stable_passing = set()
        # Flaky = seen as passed in at least one run but not in all
        union_passed = set().union(*per_run_passed) if per_run_passed else set()
        flaky = union_passed - stable_passing

        return BaselineResult(
            passing_tests=stable_passing,
            flaky_tests=flaky,
            all_tests_seen=all_seen,
            stable=len(stable_passing) >= 20 and len(flaky) <= max(5, int(0.1 * len(stable_passing))),
            raw_runs=raw_runs,
        )


def dockerhub_safe_tag(repo: RepoManifest) -> str:
    """Public helper to compute the image tag for a repo."""
    return _dockerhub_safe_tag(repo)


def instance_id_for(repo: RepoManifest) -> str:
    return _instance_id(repo)
