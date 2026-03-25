from __future__ import annotations

from benchmark_suite.agent_adapters.command_agent import CommandAgentAdapter


class OpenClawCommandAdapter(CommandAgentAdapter):
    def __init__(self, command: str, model_name: str = "openclaw-managed") -> None:
        super().__init__(name="openclaw-cmd", command=command, model_name=model_name)
