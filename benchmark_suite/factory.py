from __future__ import annotations

from benchmark_suite.agent_adapters import (
    BareLLMAdapter,
    CodexCommandAdapter,
    MockAgentAdapter,
    OpenClawCommandAdapter,
)


def build_agent(agent_name: str, agent_command: str | None = None):
    if agent_name == "mock":
        return MockAgentAdapter()
    if agent_name == "bare-llm":
        return BareLLMAdapter()
    if agent_name == "openclaw-cmd":
        if not agent_command:
            raise ValueError("--agent-command is required for openclaw-cmd")
        return OpenClawCommandAdapter(command=agent_command)
    if agent_name == "codex-cmd":
        return CodexCommandAdapter(command=agent_command or "codex")
    raise ValueError(f"Unsupported agent: {agent_name}")
