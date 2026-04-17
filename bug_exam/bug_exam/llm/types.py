"""Types shared by all LLM clients.

LLMClient is a Protocol — two concrete implementations live in
anthropic_client.py and glm_client.py.

The agent loop uses a "terminal tools" convention: callers name a subset of
tools as terminators (`emit_break_plan`, `emit_patch`, ...). When the model
calls one of those, the loop stops and the args are returned in
`AgentLoopResult.terminal_tool_args`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, runtime_checkable


@dataclass
class ToolDef:
    name: str
    description: str
    input_schema: dict              # JSON schema (OpenAI shape)


@dataclass
class AgentLoopResult:
    terminal_tool_name: str | None = None
    terminal_tool_args: dict | None = None
    final_text: str = ""
    turns: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    tool_call_log: list[tuple[str, dict, str]] = field(default_factory=list)
    error: str | None = None


@runtime_checkable
class LLMClient(Protocol):
    provider: str
    model: str

    def complete_text(self, *, system: str, user: str, max_tokens: int = 2000) -> str:
        """Simple single-turn text completion."""
        ...

    def run_agent_loop(
        self,
        *,
        system: str,
        user: str,
        tools: list[ToolDef],
        tool_handlers: dict[str, Callable[[dict], str]],
        terminal_tools: set[str],
        max_turns: int = 20,
        max_tokens: int = 4000,
    ) -> AgentLoopResult:
        """Run a multi-turn tool-use loop.

        `terminal_tools` are tool names that, when called, end the loop and
        return their args via `AgentLoopResult.terminal_tool_args`.
        """
        ...
