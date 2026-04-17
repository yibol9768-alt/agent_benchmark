"""Problem statement scrubber.

Takes the injector's draft + the failing test assertions, asks a second LLM
turn to rewrite them in a user-bug-report style that reveals no root-cause
or mutation-type information.
"""
from __future__ import annotations

import logging
from pathlib import Path

from ..llm import LLMClient, make_client

log = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent / "prompts" / "scrubber.md"


def scrub_problem_statement(
    draft: str,
    failing_test_assertions: list[str],
    client: LLMClient | None = None,
    provider: str | None = None,
    model: str | None = None,
) -> str:
    """Rewrite a draft problem statement in user-bug-report style."""
    try:
        client = client or make_client(provider=provider, model=model)
    except Exception as e:
        log.warning("scrubber: no client (%s); returning draft unchanged", e)
        return draft

    system = PROMPT_PATH.read_text()
    n_tests = len(failing_test_assertions)
    tests_info = f"({n_tests} test(s) regressed)" if n_tests else "(no failing-test count available)"
    user = (
        f"Draft problem statement (from the bug injector):\n\n{draft}\n\n"
        f"Test regression info: {tests_info}\n\n"
        f"Rewrite the problem statement per the rules. Output only the rewritten "
        f"statement — no preamble, no code fences."
    )
    try:
        text = client.complete_text(system=system, user=user, max_tokens=6000)
        return text.strip() or draft
    except Exception as e:
        log.warning("scrubber call failed: %r", e)
        return draft
