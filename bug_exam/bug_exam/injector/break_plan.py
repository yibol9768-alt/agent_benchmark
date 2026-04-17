"""Typed break-plan schema utilities.

A BreakPlan is a list of BreakStep, each declaring:
  op:        a mutation operator from the taxonomy
  file:      relative path from repo root
  line:      1-indexed line at which the op anchors
  anchor_snippet: a few characters from the original line, used by the
                  validator for AST alignment
  rationale: injector's justification

The injector emits BreakPlans as JSON; this module loads/validates them and
exposes helper constructors.
"""
from __future__ import annotations

import json

from ..schema import BreakPlan, BreakStep, MutationOp


def load_break_plan(payload: str | dict) -> BreakPlan:
    if isinstance(payload, str):
        data = json.loads(payload)
    else:
        data = payload
    steps = [
        BreakStep(
            op=MutationOp(s["op"]),
            file=s["file"],
            line=int(s["line"]),
            anchor_snippet=s.get("anchor_snippet", ""),
            rationale=s.get("rationale", ""),
        )
        for s in data["steps"]
    ]
    return BreakPlan(
        target_F=int(data["target_F"]),
        target_S=int(data["target_S"]),
        steps=steps,
        summary=data.get("summary", ""),
    )


def break_plan_to_prompt(plan: BreakPlan) -> str:
    lines = [f"Break plan (target F={plan.target_F}, S={plan.target_S}):"]
    for i, s in enumerate(plan.steps, 1):
        lines.append(f"  {i}. [{s.op.value}] {s.file}:{s.line}  -- {s.rationale}")
    if plan.summary:
        lines.append(f"Summary: {plan.summary}")
    return "\n".join(lines)
