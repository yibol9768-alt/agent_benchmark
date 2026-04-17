"""Schema + db sanity tests. Runs without Docker or network."""
import tempfile
from pathlib import Path

from bug_exam.db import Database
from bug_exam.schema import (
    BreakPlan,
    BreakStep,
    ExamInstance,
    ExamStatus,
    Language,
    MutationOp,
    RepoManifest,
    RepoStatus,
    make_instance_id,
)
from datetime import datetime


def _sample_repo() -> RepoManifest:
    now = datetime(2025, 10, 1)
    return RepoManifest(
        id="acme__widget",
        url="https://github.com/acme/widget",
        owner="acme",
        name="widget",
        language=Language.PYTHON,
        stars=123,
        size_kb=1024,
        license="MIT",
        created_at=now,
        pushed_at=now,
        base_commit="deadbeef" * 5,
        default_branch="main",
        status=RepoStatus.BASELINE_OK,
        post_cutoff=True,
        test_framework="pytest",
        baseline_test_count=42,
    )


def _sample_plan() -> BreakPlan:
    return BreakPlan(
        target_F=1, target_S=1,
        steps=[BreakStep(
            op=MutationOp.OffByOne, file="src/foo.py", line=10,
            anchor_snippet="range(n)", rationale="shrink range",
        )],
        summary="Widgets under-count by one when the list has more than 3 items.",
    )


def test_db_roundtrip_repo_and_exam():
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "status.db")
        repo = _sample_repo()
        db.upsert_repo(repo)
        got = db.get_repo(repo.id)
        assert got is not None
        assert got.url == repo.url
        assert got.post_cutoff is True

        plan = _sample_plan()
        patch = "diff --git a/src/foo.py b/src/foo.py\n--- a/src/foo.py\n+++ b/src/foo.py\n@@\n-a\n+b\n"
        iid = make_instance_id(repo.id, "trivial", "abc123")
        exam = ExamInstance(
            instance_id=iid,
            repo_id=repo.id,
            repo_url=repo.url,
            language=repo.language,
            base_commit=repo.base_commit,
            injection_patch=patch,
            break_plan=plan,
            injector_model="claude-opus-4-6",
            patch_hash="abc123",
            difficulty_band="trivial",
            F=1, S=1,
            FAIL_TO_PASS=["tests/test_foo.py::test_widget_count"],
            PASS_TO_PASS=["tests/test_foo.py::test_widget_name"],
            selected_test_files=["tests/test_foo.py"],
            problem_statement="Widgets off by one.",
            base_dockerfile_path="/tmp/base",
            instance_dockerfile_path="/tmp/inst",
            run_script_path="/tmp/run.sh",
            parser_path="/tmp/parser.py",
            test_framework="pytest",
            post_cutoff=True,
        )
        db.upsert_exam(exam)

        got_exam = db.get_exam(iid)
        assert got_exam is not None
        assert got_exam.F == 1
        assert got_exam.S == 1
        assert got_exam.FAIL_TO_PASS == exam.FAIL_TO_PASS
        assert got_exam.break_plan.target_F == 1
        assert got_exam.post_cutoff is True
        assert got_exam.status == ExamStatus.DRAFT

        assert db.exam_patch_hash_exists("abc123")
        assert not db.exam_patch_hash_exists("notahash")
