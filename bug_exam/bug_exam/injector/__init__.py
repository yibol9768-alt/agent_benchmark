"""LLM bug injector.

The injector is agentic: it explores the repo with read/grep tools, drafts a
structured BreakPlan, and synthesizes the resulting unified diff. Phase 1
implements the parallel-sample strategy (N independent draws, validator picks
the winner) to avoid the selection-bias failure mode of iterative loops.
"""
