"""Anthropic (Claude) client implementing LLMClient.

Thin wrapper around the `anthropic` python SDK. Uses the native messages
format with tool_use / tool_result blocks.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Callable

from .retry import retrying_call
from .types import AgentLoopResult, ToolDef

log = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-opus-4-6"


class AnthropicClient:
    provider = "anthropic"

    def __init__(self, model: str | None = None, api_key: str | None = None, base_url: str | None = None):
        try:
            import anthropic
        except ImportError as e:
            raise RuntimeError("anthropic package not installed; pip install anthropic") from e

        self.model = model or os.environ.get("ANTHROPIC_MODEL") or DEFAULT_MODEL
        key = api_key or os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY (or ANTHROPIC_AUTH_TOKEN) not set")
        kwargs = {"api_key": key}
        bu = base_url or os.environ.get("ANTHROPIC_BASE_URL")
        if bu:
            kwargs["base_url"] = bu
        self._client = anthropic.Anthropic(**kwargs)

    def complete_text(self, *, system: str, user: str, max_tokens: int = 2000) -> str:
        resp = retrying_call(lambda: self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        ))
        return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()

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
        anthropic_tools = [
            {"name": t.name, "description": t.description, "input_schema": t.input_schema}
            for t in tools
        ]
        messages: list[dict[str, Any]] = [{"role": "user", "content": user}]
        result = AgentLoopResult()

        for turn in range(max_turns):
            try:
                resp = retrying_call(lambda: self._client.messages.create(
                    model=self.model,
                    max_tokens=max_tokens,
                    system=system,
                    tools=anthropic_tools,
                    messages=messages,
                ))
            except Exception as e:
                result.error = f"API error on turn {turn}: {e}"
                return result

            usage = getattr(resp, "usage", None)
            if usage is not None:
                result.input_tokens += getattr(usage, "input_tokens", 0) or 0
                result.output_tokens += getattr(usage, "output_tokens", 0) or 0
            result.turns += 1

            assistant_content = [block.model_dump() for block in resp.content]
            messages.append({"role": "assistant", "content": assistant_content})

            tool_calls = [b for b in resp.content if b.type == "tool_use"]
            if not tool_calls:
                result.final_text = "".join(
                    getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text"
                )
                return result

            tool_results = []
            terminated = False
            for tc in tool_calls:
                name = tc.name
                args = tc.input or {}
                if name in terminal_tools:
                    result.terminal_tool_name = name
                    result.terminal_tool_args = args
                    result.tool_call_log.append((name, dict(args), "(terminal)"))
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tc.id,
                        "content": "recorded",
                    })
                    terminated = True
                    break
                handler = tool_handlers.get(name)
                if handler is None:
                    out = f"error: unknown tool {name}"
                else:
                    try:
                        out = handler(args)
                    except Exception as e:
                        out = f"tool error: {e}"
                out = (out or "")[:8000]
                result.tool_call_log.append((name, dict(args), out[:500]))
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tc.id,
                    "content": out,
                })

            messages.append({"role": "user", "content": tool_results})
            if terminated:
                return result
            if resp.stop_reason == "end_turn":
                break

        return result
