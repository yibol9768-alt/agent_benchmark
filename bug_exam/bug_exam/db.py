"""Sqlite status DB.

Five tables (repos, envbuilds, exams, runs, grades). Every pipeline stage
reads and writes this DB; it's the single source of truth for resumability.

JSON-encoded blobs are fine for nested structures (BreakPlan, test lists) —
we never query inside them.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .schema import (
    BreakPlan,
    ExamInstance,
    ExamStatus,
    Grade,
    Language,
    RepoManifest,
    RepoStatus,
    RunStatus,
    SolverResult,
)


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS repos (
    id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    owner TEXT NOT NULL,
    name TEXT NOT NULL,
    language TEXT NOT NULL,
    stars INTEGER,
    size_kb INTEGER,
    license TEXT,
    created_at TEXT,
    pushed_at TEXT,
    base_commit TEXT,
    default_branch TEXT,
    status TEXT NOT NULL,
    post_cutoff INTEGER NOT NULL DEFAULT 0,
    test_framework TEXT,
    baseline_test_count INTEGER,
    meta_json TEXT
);

CREATE TABLE IF NOT EXISTS envbuilds (
    repo_id TEXT PRIMARY KEY REFERENCES repos(id),
    base_image_tag TEXT,
    instance_image_tag TEXT,
    dockerfile_base TEXT,
    dockerfile_instance TEXT,
    status TEXT NOT NULL,
    log_path TEXT,
    built_at TEXT
);

CREATE TABLE IF NOT EXISTS exams (
    instance_id TEXT PRIMARY KEY,
    repo_id TEXT NOT NULL REFERENCES repos(id),
    difficulty_band TEXT NOT NULL,
    F INTEGER NOT NULL,
    S INTEGER NOT NULL,
    patch_hash TEXT NOT NULL,
    injection_patch TEXT NOT NULL,
    break_plan_json TEXT NOT NULL,
    problem_statement TEXT NOT NULL,
    fail_to_pass_json TEXT NOT NULL,
    pass_to_pass_json TEXT NOT NULL,
    selected_test_files_json TEXT NOT NULL,
    injector_model TEXT NOT NULL,
    base_dockerfile_path TEXT,
    instance_dockerfile_path TEXT,
    run_script_path TEXT,
    parser_path TEXT,
    test_framework TEXT,
    before_repo_set_cmd TEXT,
    post_cutoff INTEGER NOT NULL DEFAULT 0,
    call_graph_radius INTEGER,
    mutation_op_histogram_json TEXT,
    status TEXT NOT NULL,
    created_at TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_exams_patch_hash ON exams(patch_hash);

CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    exam_id TEXT NOT NULL REFERENCES exams(instance_id),
    solver_name TEXT NOT NULL,
    patch_diff TEXT,
    trajectory_path TEXT,
    status TEXT NOT NULL,
    wall_clock_s REAL,
    error_message TEXT,
    token_usage_json TEXT,
    started_at TEXT,
    finished_at TEXT,
    UNIQUE (exam_id, solver_name)
);

CREATE TABLE IF NOT EXISTS grades (
    run_id TEXT PRIMARY KEY REFERENCES runs(id),
    exam_id TEXT NOT NULL REFERENCES exams(instance_id),
    solver_name TEXT NOT NULL,
    passed_tests_json TEXT NOT NULL,
    failed_tests_json TEXT NOT NULL,
    f2p_pass INTEGER NOT NULL,
    p2p_pass INTEGER NOT NULL,
    final_passed INTEGER NOT NULL,
    stderr_excerpt TEXT
);

CREATE INDEX IF NOT EXISTS ix_runs_exam ON runs(exam_id);
CREATE INDEX IF NOT EXISTS ix_grades_solver ON grades(solver_name);
"""


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self.connect() as conn:
            conn.executescript(SCHEMA_SQL)
            conn.commit()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
        finally:
            conn.close()

    # --- repos -------------------------------------------------------------

    def upsert_repo(self, repo: RepoManifest) -> None:
        with self._lock, self.connect() as conn:
            conn.execute(
                """
                INSERT INTO repos(id, url, owner, name, language, stars, size_kb,
                                  license, created_at, pushed_at, base_commit,
                                  default_branch, status, post_cutoff,
                                  test_framework, baseline_test_count)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status=excluded.status,
                    base_commit=excluded.base_commit,
                    test_framework=excluded.test_framework,
                    baseline_test_count=excluded.baseline_test_count
                """,
                (
                    repo.id, repo.url, repo.owner, repo.name, repo.language.value,
                    repo.stars, repo.size_kb, repo.license,
                    repo.created_at.isoformat(), repo.pushed_at.isoformat(),
                    repo.base_commit, repo.default_branch, repo.status.value,
                    int(repo.post_cutoff), repo.test_framework, repo.baseline_test_count,
                ),
            )

    def set_repo_status(self, repo_id: str, status: RepoStatus) -> None:
        with self._lock, self.connect() as conn:
            conn.execute("UPDATE repos SET status=? WHERE id=?", (status.value, repo_id))

    def get_repo(self, repo_id: str) -> RepoManifest | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM repos WHERE id=?", (repo_id,)).fetchone()
            return _row_to_repo(row) if row else None

    def list_repos(self, status: RepoStatus | None = None) -> list[RepoManifest]:
        with self.connect() as conn:
            if status:
                rows = conn.execute("SELECT * FROM repos WHERE status=?", (status.value,)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM repos").fetchall()
            return [_row_to_repo(r) for r in rows]

    # --- envbuilds ---------------------------------------------------------

    def upsert_envbuild(
        self,
        repo_id: str,
        base_image_tag: str,
        instance_image_tag: str,
        dockerfile_base: str,
        dockerfile_instance: str,
        status: str,
        log_path: str,
    ) -> None:
        from datetime import datetime as _dt
        with self._lock, self.connect() as conn:
            conn.execute(
                """
                INSERT INTO envbuilds(repo_id, base_image_tag, instance_image_tag,
                                      dockerfile_base, dockerfile_instance,
                                      status, log_path, built_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(repo_id) DO UPDATE SET
                    base_image_tag=excluded.base_image_tag,
                    instance_image_tag=excluded.instance_image_tag,
                    status=excluded.status,
                    log_path=excluded.log_path,
                    built_at=excluded.built_at
                """,
                (repo_id, base_image_tag, instance_image_tag, dockerfile_base,
                 dockerfile_instance, status, log_path, _dt.utcnow().isoformat()),
            )

    def get_envbuild(self, repo_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM envbuilds WHERE repo_id=?", (repo_id,)).fetchone()
            return dict(row) if row else None

    # --- exams -------------------------------------------------------------

    def upsert_exam(self, exam: ExamInstance) -> None:
        with self._lock, self.connect() as conn:
            conn.execute(
                """
                INSERT INTO exams(instance_id, repo_id, difficulty_band, F, S,
                                  patch_hash, injection_patch, break_plan_json,
                                  problem_statement, fail_to_pass_json,
                                  pass_to_pass_json, selected_test_files_json,
                                  injector_model, base_dockerfile_path,
                                  instance_dockerfile_path, run_script_path,
                                  parser_path, test_framework, before_repo_set_cmd,
                                  post_cutoff, call_graph_radius,
                                  mutation_op_histogram_json, status, created_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(instance_id) DO UPDATE SET
                    status=excluded.status,
                    injection_patch=excluded.injection_patch,
                    problem_statement=excluded.problem_statement
                """,
                (
                    exam.instance_id, exam.repo_id, exam.difficulty_band,
                    exam.F, exam.S, exam.patch_hash, exam.injection_patch,
                    exam.break_plan.model_dump_json(), exam.problem_statement,
                    json.dumps(exam.FAIL_TO_PASS), json.dumps(exam.PASS_TO_PASS),
                    json.dumps(exam.selected_test_files), exam.injector_model,
                    exam.base_dockerfile_path, exam.instance_dockerfile_path,
                    exam.run_script_path, exam.parser_path, exam.test_framework,
                    exam.before_repo_set_cmd, int(exam.post_cutoff),
                    exam.call_graph_radius,
                    json.dumps(exam.mutation_op_histogram),
                    exam.status.value, exam.created_at.isoformat(),
                ),
            )

    def list_exams(self, status: ExamStatus | None = None) -> list[ExamInstance]:
        with self.connect() as conn:
            if status:
                rows = conn.execute("SELECT * FROM exams WHERE status=?", (status.value,)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM exams").fetchall()
            return [_row_to_exam(r, self) for r in rows]

    def get_exam(self, instance_id: str) -> ExamInstance | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM exams WHERE instance_id=?", (instance_id,)).fetchone()
            return _row_to_exam(row, self) if row else None

    def exam_patch_hash_exists(self, patch_hash: str) -> bool:
        with self.connect() as conn:
            row = conn.execute("SELECT 1 FROM exams WHERE patch_hash=?", (patch_hash,)).fetchone()
            return row is not None

    def set_exam_status(self, instance_id: str, status: ExamStatus) -> None:
        with self._lock, self.connect() as conn:
            conn.execute("UPDATE exams SET status=? WHERE instance_id=?", (status.value, instance_id))

    # --- runs --------------------------------------------------------------

    def upsert_run(
        self,
        run_id: str,
        exam_id: str,
        solver_name: str,
        result: SolverResult | None,
        status: RunStatus,
        error_message: str | None = None,
    ) -> None:
        from datetime import datetime as _dt
        patch = result.patch if result else ""
        wall = result.wall_clock_s if result else None
        tokens = json.dumps(result.token_usage) if result else None
        traj = result.trajectory_path if result else None
        with self._lock, self.connect() as conn:
            conn.execute(
                """
                INSERT INTO runs(id, exam_id, solver_name, patch_diff,
                                 trajectory_path, status, wall_clock_s,
                                 error_message, token_usage_json,
                                 started_at, finished_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(exam_id, solver_name) DO UPDATE SET
                    patch_diff=excluded.patch_diff,
                    trajectory_path=excluded.trajectory_path,
                    status=excluded.status,
                    wall_clock_s=excluded.wall_clock_s,
                    error_message=excluded.error_message,
                    token_usage_json=excluded.token_usage_json,
                    finished_at=excluded.finished_at
                """,
                (run_id, exam_id, solver_name, patch, traj, status.value,
                 wall, error_message, tokens, _dt.utcnow().isoformat(), _dt.utcnow().isoformat()),
            )

    def list_runs(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM runs").fetchall()
            return [dict(r) for r in rows]

    # --- grades ------------------------------------------------------------

    def upsert_grade(self, grade: Grade) -> None:
        with self._lock, self.connect() as conn:
            conn.execute(
                """
                INSERT INTO grades(run_id, exam_id, solver_name, passed_tests_json,
                                   failed_tests_json, f2p_pass, p2p_pass,
                                   final_passed, stderr_excerpt)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    passed_tests_json=excluded.passed_tests_json,
                    failed_tests_json=excluded.failed_tests_json,
                    f2p_pass=excluded.f2p_pass,
                    p2p_pass=excluded.p2p_pass,
                    final_passed=excluded.final_passed,
                    stderr_excerpt=excluded.stderr_excerpt
                """,
                (
                    grade.run_id, grade.exam_id, grade.solver_name,
                    json.dumps(grade.passed_tests), json.dumps(grade.failed_tests),
                    int(grade.f2p_pass), int(grade.p2p_pass),
                    int(grade.final_passed), grade.stderr_excerpt,
                ),
            )

    def list_grades(self) -> list[Grade]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM grades").fetchall()
            return [
                Grade(
                    run_id=r["run_id"],
                    exam_id=r["exam_id"],
                    solver_name=r["solver_name"],
                    passed_tests=json.loads(r["passed_tests_json"]),
                    failed_tests=json.loads(r["failed_tests_json"]),
                    f2p_pass=bool(r["f2p_pass"]),
                    p2p_pass=bool(r["p2p_pass"]),
                    final_passed=bool(r["final_passed"]),
                    stderr_excerpt=r["stderr_excerpt"] or "",
                )
                for r in rows
            ]


# Row -> model helpers -------------------------------------------------------

def _row_to_repo(row: sqlite3.Row) -> RepoManifest:
    from datetime import datetime as _dt
    return RepoManifest(
        id=row["id"], url=row["url"], owner=row["owner"], name=row["name"],
        language=Language(row["language"]), stars=row["stars"] or 0,
        size_kb=row["size_kb"] or 0, license=row["license"],
        created_at=_dt.fromisoformat(row["created_at"]),
        pushed_at=_dt.fromisoformat(row["pushed_at"]),
        base_commit=row["base_commit"] or "",
        default_branch=row["default_branch"] or "main",
        status=RepoStatus(row["status"]),
        post_cutoff=bool(row["post_cutoff"]),
        test_framework=row["test_framework"],
        baseline_test_count=row["baseline_test_count"],
    )


def _row_to_exam(row: sqlite3.Row, db: "Database") -> ExamInstance:
    from datetime import datetime as _dt
    repo = db.get_repo(row["repo_id"])
    if not repo:
        raise RuntimeError(f"Exam {row['instance_id']} references missing repo {row['repo_id']}")
    break_plan = BreakPlan.model_validate_json(row["break_plan_json"])
    return ExamInstance(
        instance_id=row["instance_id"],
        repo_id=row["repo_id"],
        repo_url=repo.url,
        language=repo.language,
        base_commit=repo.base_commit,
        injection_patch=row["injection_patch"],
        break_plan=break_plan,
        injector_model=row["injector_model"],
        patch_hash=row["patch_hash"],
        difficulty_band=row["difficulty_band"],
        F=row["F"], S=row["S"],
        FAIL_TO_PASS=json.loads(row["fail_to_pass_json"]),
        PASS_TO_PASS=json.loads(row["pass_to_pass_json"]),
        selected_test_files=json.loads(row["selected_test_files_json"]),
        problem_statement=row["problem_statement"],
        base_dockerfile_path=row["base_dockerfile_path"] or "",
        instance_dockerfile_path=row["instance_dockerfile_path"] or "",
        run_script_path=row["run_script_path"] or "",
        parser_path=row["parser_path"] or "",
        test_framework=row["test_framework"] or "",
        before_repo_set_cmd=row["before_repo_set_cmd"] or "",
        post_cutoff=bool(row["post_cutoff"]),
        created_at=_dt.fromisoformat(row["created_at"]),
        call_graph_radius=row["call_graph_radius"],
        mutation_op_histogram=json.loads(row["mutation_op_histogram_json"] or "{}"),
        status=ExamStatus(row["status"]),
    )
