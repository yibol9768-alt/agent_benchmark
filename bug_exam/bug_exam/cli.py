"""Bug Exam Bench CLI.

Each subcommand is an idempotent pipeline stage; progress is tracked in
data/status.db. Re-run a stage and it picks up where it left off.

Examples:

    # Phase 1 smoke flow
    bug-exam harvest  --language python --max 10
    bug-exam envbuild --limit 10
    bug-exam inject   --bands trivial,easy --n-draws 4
    bug-exam freeze   --name v0_smoke
    bug-exam solve    --solvers claude_direct,mini_swe_agent,aider
    bug-exam grade
    bug-exam score
    bug-exam report
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import typer
import yaml

from .db import Database
from .orchestrator import pipeline

app = typer.Typer(add_completion=False, no_args_is_help=True)
log = logging.getLogger("bug_exam")


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CONFIGS = ROOT / "configs"


def _db() -> Database:
    return Database(DATA / "status.db")


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
        datefmt="%H:%M:%S",
    )


@app.callback()
def main(verbose: bool = typer.Option(False, "--verbose", "-v")) -> None:
    _setup_logging(verbose)


@app.command()
def harvest(
    language: str | None = typer.Option(None, help="Restrict to one language"),
    max: int | None = typer.Option(None, help="Max candidates to fetch"),
):
    """Query GitHub for fresh repos and record them as CANDIDATE."""
    n = pipeline.stage_harvest(_db(), language=language, max_candidates=max)
    typer.echo(f"harvested {n} candidates")


@app.command()
def envbuild(
    limit: int | None = typer.Option(None, help="Max repos to build this invocation"),
):
    """Clone, detect framework, build docker image, run baseline tests."""
    n = pipeline.stage_envbuild(_db(), limit=limit)
    typer.echo(f"{n} repos reached BASELINE_OK")


@app.command()
def inject(
    bands: str = typer.Option("trivial,easy", help="Comma-separated band ids"),
    n_draws: int = typer.Option(4, help="Parallel samples per exam"),
    model: str = typer.Option("claude-opus-4-6", help="Injector model"),
    limit_repos: int | None = typer.Option(None, help="Max repos to process"),
):
    """Inject bugs and run the 8 validation gates. Produces VALIDATED exams."""
    cfg = yaml.safe_load((CONFIGS / "difficulty_bands.yaml").read_text())
    band_lookup = {b["id"]: (b["F"], b["S"], b["id"]) for b in cfg["bands"]}
    selected = []
    for bid in bands.split(","):
        bid = bid.strip()
        if bid not in band_lookup:
            typer.echo(f"unknown band {bid}", err=True)
            raise typer.Exit(2)
        selected.append(band_lookup[bid])
    n = pipeline.stage_inject_and_validate(
        _db(), bands=selected, n_draws=n_draws,
        injector_model=model, limit_repos=limit_repos,
    )
    typer.echo(f"{n} exams validated")


@app.command()
def freeze(name: str = typer.Option(..., help="Exam set name, e.g. v0_smoke")):
    """Snapshot all VALIDATED exams into a named JSONL release."""
    path = pipeline.stage_freeze(_db(), exam_set_name=name)
    typer.echo(f"wrote {path}")


@app.command()
def solve(
    solvers: str = typer.Option(..., help="Comma-separated solver names"),
    limit_exams: int | None = typer.Option(None),
):
    """Run each solver against every frozen exam."""
    names = [s.strip() for s in solvers.split(",") if s.strip()]
    n = pipeline.stage_solve(_db(), solver_names=names, limit_exams=limit_exams)
    typer.echo(f"{n} runs recorded")


@app.command()
def grade():
    """Evaluate every completed run by running the test suite."""
    n = pipeline.stage_grade(_db())
    typer.echo(f"{n} grades computed")


@app.command()
def score(out: Path | None = typer.Option(None)):
    """Fit Bradley-Terry + Elo and write the leaderboard JSON."""
    path = pipeline.stage_score(_db(), out_dir=out)
    typer.echo(f"wrote {path}")


@app.command()
def report():
    """Pretty-print the current leaderboard."""
    lb_path = DATA / "runs" / "leaderboard" / "leaderboard.json"
    if not lb_path.exists():
        typer.echo("no leaderboard yet — run `bug-exam score` first", err=True)
        raise typer.Exit(1)
    payload = json.loads(lb_path.read_text())
    typer.echo(f"Bug Exam Bench leaderboard  ({payload['n_runs']} runs, {payload['n_exams']} exams)")
    typer.echo("")
    header = f"{'solver':<20} {'BT':>7}  {'BT 95% CI':>18}  {'Elo':>7}  {'pass@1':>8}"
    typer.echo(header)
    typer.echo("-" * len(header))
    for e in payload["solvers"]:
        ci = f"[{e['bt_ci_lo']:+.2f}, {e['bt_ci_hi']:+.2f}]"
        typer.echo(
            f"{e['solver_name']:<20} {e['bt_rating']:+7.3f}  {ci:>18}  "
            f"{e['elo_rating']:7.1f}  {e['pass_rate_overall']*100:7.1f}%"
        )


if __name__ == "__main__":
    app()
