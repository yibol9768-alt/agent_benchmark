# M1: SWE-Bench Pro integration — design

## Goal
Prove ONE end-to-end self-play cycle on a real repo:
SWE-Bench Pro qutebrowser instance →
bug_exam injector synthesizes a NEW bug (not the upstream PR's bug) →
validator gates accept it →
freeze ExamInstance →
2 solvers attempt fixes →
SWE-Bench Pro Docker harness grades each patch.

## What we reuse vs rewrite

| Component                                  | Reused as-is                                                 | New (M1)                                                     |
| ------------------------------------------ | ------------------------------------------------------------ | ------------------------------------------------------------ |
| Per-instance test image                    | `jefzda/sweap-images:<tag>` from Docker Hub                   |                                                              |
| `run_script.sh`, `parser.py`               | `SWE-bench_Pro-os/run_scripts/<id>/`                          |                                                              |
| Entryscript shape (env + reset + apply…)  | `bug_exam/evaluator/entryscript.py`                           |                                                              |
| Container driver                           | `bug_exam/evaluator/docker_runner.py`                         |                                                              |
| junitxml/`output.json` parsing             | Per-instance `parser.py` (called by entryscript)              |                                                              |
| Injector (planner + executor + n_draws)    | `bug_exam/injector/agent.py` — works on any git checkout      |                                                              |
| Solvers `claude_direct`, `openhands`       | unchanged; same SolverAdapter API                             |                                                              |
| Validator gates                            | G1 (apply), G3/G4 (file/step counts), G6 (1≤F2P≤10)          | G7 relaxed (≥90% kept) because we only run *selected_test_files*, not full suite, so any unrelated flake would falsely break P2P |
| Schema                                     | `ExamInstance`, `BreakPlan`, etc.                             |                                                              |
| SWE-Bench Pro row → bug_exam objects       |                                                              | `bug_exam/adapters/swebench_pro_source.py`                   |
| Per-instance image runner shim             |                                                              | `bug_exam/evaluator/swe_bench_pro_runner.py` (thin wrapper)  |
| End-to-end M1 driver                       |                                                              | `scripts/run_swebench_pro_m1.py`                             |

## Key design decisions

**1. Entry test set = SWE-Bench Pro's `selected_test_files_to_run`.**
We deliberately do NOT run the full repo test suite. The qutebrowser image's
official `run_script.sh` accepts a comma-separated list and runs only those
files. This is fast (~1 min on qutebrowser) and means our F2P set is computed
against `tests/unit/utils/test_log.py` + `tests/unit/utils/test_qtlog.py`
(56 tests). The injector is told to break code that those tests cover —
implicitly, by reading the test files.

**2. Bug location is unconstrained.** The injector can mutate any source file
in the repo. It will normally pick something near the imported modules from
the test files. If a draw breaks no test in the selected set, gate G6 fails
(|F2P|=0) and we move to the next draw.

**3. F2P is the NEW post-injection failing tests, P2P is the rest.** We
discard the upstream row's `FAIL_TO_PASS` / `PASS_TO_PASS` entirely (those
were for the original PR's bug). Recomputed via baseline vs candidate eval.

**4. Skip bug_exam's own `validate_injection`.** That function is wired to
bug_exam's own envbuild/run_script layout (`data/run_scripts/bexam__…`),
which we don't have for SWE-Pro repos. The M1 driver inlines the equivalent
of G1+G6+G7 directly using our `swe_bench_pro_runner` for the in-Docker test
runs. AST-level G2/G4 are skipped for M1 — we pay for them in M2.

**5. No mutation of the upstream gold patch.** We start from clean
`base_commit` (pre-PR state). The original bug is still latent in upstream
HEAD at this commit, but the SWE-Pro test_patch is not applied — we want the
bug_exam injector's bug to be the only intentional fault.

**6. Image pull responsibility lives outside bug_exam.** The remote Docker
daemon must already have `HTTP_PROXY` exported in
`/etc/systemd/system/docker.service.d/http-proxy.conf` (per project CLAUDE.md).
The runner falls back to `client.images.get(image_tag)` first; only pulls
if missing.

## Pipeline (driver)

```
load_instance(jsonl, instance_id)
  └─> SwebenchProInstance{repo, base_commit, image_tag, run_script_path,
                          parser_path, before_repo_set_cmd, …}

checkout_repo(inst, workdir)              # git clone + checkout base_commit
exam_skel = inst.to_exam_skeleton()       # ExamInstance, empty patch

# Stage A: baseline in image
baseline = run_swebench_pro_exam(exam_skel, patch_kind="baseline")
baseline_passing = set(baseline.passed_tests)         # ground truth

# Stage B: inject N candidates
draws = draw_injections(workdir, F=1, S=1, n_draws=4)

# Stage C: pick first draw passing G1 + G6 + relaxed-G7
for draw in draws:
    if not git apply --check draw.diff: continue
    cand_eval = run_swebench_pro_exam(skel.with(injection=draw.diff),
                                       patch_kind="bug_only")
    new_failing = baseline_passing - cand_eval.passed
    if not (1 <= |new_failing| <= 10): continue
    if |baseline_passing - new_failing - cand_eval.passed| > 10%: continue
    chosen = draw; break

exam = finalize_exam(inst, draw.diff, plan, F2P=new_failing, P2P=rest)

# Stage D: solvers
for name in ["claude_direct", "openhands"]:
    solver_workdir = clone(workdir) + apply(injection)
    sres = solver.solve(exam, solver_workdir)
    grade = run_swebench_pro_exam(exam, solver_patch=sres.patch,
                                   patch_kind="solver")
    passed = (F2P ⊆ grade.passed) ∧ (P2P ⊆ grade.passed)
```

## Output: `dumps/swebench_pro_m1/<repo>/`

- `summary.json` — top-level run metadata + per-solver result
- `exam.json` — frozen ExamInstance (input to solver eval, anti-contam record)
- `injection.diff` — the synthesized bug
- `<solver>.diff` — each solver's candidate fix
- gate_log embedded in summary, including for rejected draws

## Constraints & known limits (input to M2)

- Only one (F, S) band — we don't sweep difficulty in M1.
- Problem statement = injector's own `summary` field (not scrubbed). Solvers
  may see hints. Scrubber wiring is M2.
- G7 uses 90% threshold not strict subset — to absorb env-level test flakes
  on first run; tighten in M2 once we measure repeat-run noise.
- One repo, one instance. The injector is not yet aware of "selected_test_files"
  scoping, so it may waste draws on files no selected test exercises. M2:
  feed the selected file list into the planner prompt.
- OpenHands large-repo token budget may blow up on qutebrowser. Reported as
  a non-fatal solver error; claude_direct PASSING alone meets M1 criteria.
