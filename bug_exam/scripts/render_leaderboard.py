"""Render BT / Elo leaderboard from a summary.jsonl file (one ExamRun per row).

Each row must carry at least:
  exam_id, solver_name, final_passed, band (optional), instance_id (optional)

Outputs (under --out-dir):
  bt.json         — Bradley-Terry ratings + 95% bootstrap CI
  elo.json        — streaming Elo final ratings + timeline
  table.md        — human-readable markdown summary
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from bug_exam.scoring.bradley_terry import build_pairwise_from_grades, fit as bt_fit
from bug_exam.scoring.elo import EloState


def _load_rows(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _pairwise_with_timeline(rows: list[dict]) -> tuple[list[tuple[str, str, float]], list[dict]]:
    """Deterministic per-exam pairwise outcomes, in stream order."""
    by_exam: dict[str, list[tuple[str, bool]]] = {}
    order: list[str] = []
    for r in rows:
        eid = r["exam_id"]
        if eid not in by_exam:
            by_exam[eid] = []
            order.append(eid)
        by_exam[eid].append((r["solver_name"], bool(r.get("final_passed"))))
    pairs: list[tuple[str, str, float]] = []
    timeline: list[dict] = []
    elo = EloState()
    for eid in order:
        rs = by_exam[eid]
        for i in range(len(rs)):
            for j in range(i + 1, len(rs)):
                a, pa = rs[i]
                b, pb = rs[j]
                if pa and not pb:
                    o = 1.0
                elif pb and not pa:
                    o = 0.0
                else:
                    o = 0.5
                pairs.append((a, b, o))
                elo.update_pair(a, b, o)
                timeline.append({
                    "exam_id": eid, "a": a, "b": b, "outcome": o,
                    "ratings": dict(elo.ratings),
                })
    return pairs, timeline


def render(summary_path: Path, out_dir: Path) -> dict:
    rows = _load_rows(summary_path)
    if not rows:
        raise SystemExit(f"no rows in {summary_path}")

    solvers = sorted({r["solver_name"] for r in rows})
    pairs, timeline = _pairwise_with_timeline(rows)
    bt = bt_fit(solvers, pairs, bootstrap=500, seed=0)

    final_elo = timeline[-1]["ratings"] if timeline else {}

    # per-solver pass rate (overall) + per-band
    overall_pass: dict[str, dict[str, int]] = defaultdict(lambda: {"pass": 0, "n": 0})
    band_pass: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: {"pass": 0, "n": 0})
    for r in rows:
        s = r["solver_name"]
        overall_pass[s]["n"] += 1
        if r.get("final_passed"):
            overall_pass[s]["pass"] += 1
        band = r.get("band", "unknown")
        band_pass[(s, band)]["n"] += 1
        if r.get("final_passed"):
            band_pass[(s, band)]["pass"] += 1

    bt_json = {
        "solvers": solvers,
        "ratings": bt.ratings,
        "ci_lo": bt.ci_lo,
        "ci_hi": bt.ci_hi,
        "n_pairs": bt.n_pairs,
    }
    elo_json = {
        "final": final_elo,
        "timeline": timeline,
    }
    overall = {s: {"pass": d["pass"], "n": d["n"],
                   "rate": (d["pass"] / d["n"]) if d["n"] else 0.0}
               for s, d in overall_pass.items()}
    per_band = {f"{s}::{b}": {"pass": v["pass"], "n": v["n"],
                               "rate": (v["pass"] / v["n"]) if v["n"] else 0.0}
                 for (s, b), v in band_pass.items()}

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "bt.json").write_text(json.dumps(bt_json, indent=2))
    (out_dir / "elo.json").write_text(json.dumps(elo_json, indent=2))

    # markdown table
    lines = ["# BEB Leaderboard\n",
             f"- ExamRuns: {len(rows)}  |  Pairs: {bt.n_pairs}\n",
             f"- Solvers: {', '.join(solvers)}\n",
             "\n## Bradley-Terry (centered log-ability, 95% bootstrap CI)\n",
             "| solver | BT | 95% CI | Elo | pass-rate |", "|---|---:|---|---:|---:|"]
    ordered = sorted(solvers, key=lambda s: bt.ratings.get(s, 0.0), reverse=True)
    for s in ordered:
        rating = bt.ratings.get(s, 0.0)
        lo = bt.ci_lo.get(s, 0.0)
        hi = bt.ci_hi.get(s, 0.0)
        elo_r = final_elo.get(s, 1500.0)
        rate = overall.get(s, {}).get("rate", 0.0)
        n = overall.get(s, {}).get("n", 0)
        p = overall.get(s, {}).get("pass", 0)
        lines.append(f"| `{s}` | {rating:+.3f} | [{lo:+.3f}, {hi:+.3f}] | {elo_r:.1f} | {rate*100:.1f}% ({p}/{n}) |")
    lines.append("\n## Per-band pass-rate\n")
    bands = sorted({b for (_, b) in band_pass})
    header = "| solver | " + " | ".join(bands) + " |"
    sep = "|---|" + "---|" * len(bands)
    lines.append(header)
    lines.append(sep)
    for s in ordered:
        cells = []
        for b in bands:
            v = band_pass.get((s, b), {"pass": 0, "n": 0})
            if v["n"]:
                cells.append(f"{100*v['pass']/v['n']:.0f}% ({v['pass']}/{v['n']})")
            else:
                cells.append("-")
        lines.append(f"| `{s}` | " + " | ".join(cells) + " |")
    (out_dir / "table.md").write_text("\n".join(lines) + "\n")

    return {"bt": bt_json, "elo_final": final_elo, "overall": overall, "per_band": per_band}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--summary", required=True)
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()
    out = render(Path(args.summary), Path(args.out_dir))
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
