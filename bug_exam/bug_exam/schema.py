"""Pydantic models for the Bug Exam Bench pipeline.

Everything that flows between pipeline stages is a typed dataclass. No
stringified JSON in CSV cells, no `eval()` of schema fields — the clean-room
rewrite of SWE-bench Pro's schema.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RepoStatus(str, Enum):
    CANDIDATE = "candidate"
    CLONED = "cloned"
    BUILT = "built"
    BASELINE_OK = "baseline_ok"
    REJECTED = "rejected"
    USED = "used"


class ExamStatus(str, Enum):
    DRAFT = "draft"              # injector proposed, not yet validated
    VALIDATED = "validated"      # passed all gates
    REJECTED = "rejected"        # failed a gate
    FROZEN = "frozen"            # locked in an exam set


class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    ERRORED = "errored"
    TIMEOUT = "timeout"


class Language(str, Enum):
    PYTHON = "python"
    JAVASCRIPT = "javascript"
    TYPESCRIPT = "typescript"
    GO = "go"
    JAVA = "java"
    KOTLIN = "kotlin"
    RUST = "rust"
    RUBY = "ruby"


class RepoManifest(BaseModel):
    """A harvested GitHub repository, pinned at a specific commit."""
    model_config = ConfigDict(extra="forbid")

    id: str                               # e.g. "owner__name"
    url: str                              # full GitHub URL
    owner: str
    name: str
    language: Language
    stars: int
    size_kb: int
    license: str | None
    created_at: datetime
    pushed_at: datetime
    base_commit: str                      # frozen HEAD SHA at harvest time
    default_branch: str
    status: RepoStatus = RepoStatus.CANDIDATE
    post_cutoff: bool = False             # created strictly after contamination cutoff
    test_framework: str | None = None     # pytest, mocha, gotest, ...
    baseline_test_count: int | None = None


class MutationOp(str, Enum):
    OffByOne = "OffByOne"
    InvertedCondition = "InvertedCondition"
    SwappedArgs = "SwappedArgs"
    RemovedGuard = "RemovedGuard"
    WrongBinaryOperator = "WrongBinaryOperator"
    DroppedReturn = "DroppedReturn"
    SwitchedConstant = "SwitchedConstant"
    FlippedBoolean = "FlippedBoolean"
    WrongExceptionType = "WrongExceptionType"
    MissingAwait = "MissingAwait"
    WrongLoopBound = "WrongLoopBound"
    StateReorder = "StateReorder"
    ShadowedVariable = "ShadowedVariable"
    IncorrectTypeCast = "IncorrectTypeCast"
    OmittedSideEffect = "OmittedSideEffect"


class BreakStep(BaseModel):
    """A single typed mutation operation declared by the injector."""
    model_config = ConfigDict(extra="forbid")

    op: MutationOp
    file: str                             # relative path from repo root
    line: int                             # 1-indexed line at which the op anchors
    anchor_snippet: str                   # a few characters from the original line, for AST matching
    rationale: str                        # injector's justification


class BreakPlan(BaseModel):
    """The injector's full break plan, emitted before patch synthesis."""
    model_config = ConfigDict(extra="forbid")

    target_F: int
    target_S: int
    steps: list[BreakStep]
    summary: str


class DifficultyBand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    F: int
    S: int
    label: str


class ExamInstance(BaseModel):
    """A frozen exam: a repo + an injected bug + validation artifacts.

    This is the input to every solver. Designed to be a superset of SWE-bench
    Pro's instance schema so that a subset can be exported compatible with
    existing tools.
    """
    model_config = ConfigDict(extra="forbid")

    instance_id: str                      # globally unique
    repo_id: str
    repo_url: str
    language: Language
    base_commit: str                      # repo HEAD before bug
    buggy_commit: str | None = None       # the commit applied to produce the buggy state (optional — patch is the source of truth)

    # the bug
    injection_patch: str                  # unified diff
    break_plan: BreakPlan
    injector_model: str
    patch_hash: str                       # sha256 hex (no "sha256:" prefix) of injection_patch

    # difficulty
    difficulty_band: str
    F: int                                # actual files_modified from diff
    S: int                                # actual validated break_step count

    # test oracle
    FAIL_TO_PASS: list[str]
    PASS_TO_PASS: list[str]
    selected_test_files: list[str]

    # solver-facing problem statement (scrubbed)
    problem_statement: str

    # environment
    base_dockerfile_path: str             # relative to data/dockerfiles/base/
    instance_dockerfile_path: str
    run_script_path: str
    parser_path: str
    test_framework: str
    before_repo_set_cmd: str = ""

    # metadata for stratification
    post_cutoff: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)

    # secondary features logged for analysis
    call_graph_radius: int | None = None
    mutation_op_histogram: dict[str, int] = Field(default_factory=dict)

    status: ExamStatus = ExamStatus.DRAFT

    def compute_patch_hash(self) -> str:
        return hashlib.sha256(self.injection_patch.encode("utf-8")).hexdigest()


class SolverResult(BaseModel):
    """The raw output of a solver run — before grading."""
    model_config = ConfigDict(extra="forbid")

    solver_name: str
    exam_id: str
    patch: str                            # the candidate fix diff
    trajectory_path: str | None = None    # log / conversation trace
    wall_clock_s: float
    token_usage: dict[str, int] = Field(default_factory=dict)
    errored: bool = False
    error_message: str | None = None


class Grade(BaseModel):
    """The grade for a single (exam, solver) run after test execution."""
    model_config = ConfigDict(extra="forbid")

    run_id: str
    exam_id: str
    solver_name: str
    passed_tests: list[str]
    failed_tests: list[str]
    f2p_pass: bool
    p2p_pass: bool
    final_passed: bool
    stderr_excerpt: str = ""


class LeaderboardEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    solver_name: str
    bt_rating: float                      # Bradley-Terry headline
    bt_ci_lo: float
    bt_ci_hi: float
    elo_rating: float
    pass_rate_overall: float
    pass_rate_by_band: dict[str, float]
    pass_rate_by_language: dict[str, float] = Field(default_factory=dict)
    n_runs: int


# Convenience helpers --------------------------------------------------------

def make_instance_id(repo_id: str, band_id: str, patch_hash: str) -> str:
    # Strip any legacy "sha256:" prefix, take 16 hex chars; no colons in id
    # so docker -v volume specs and filesystem paths don't break.
    h = patch_hash.split(":", 1)[-1]
    short_hash = h[:16]
    return f"exam__{repo_id}__{band_id}__{short_hash}"


def load_yaml(path: Path) -> dict[str, Any]:
    import yaml
    with open(path) as f:
        return yaml.safe_load(f)
