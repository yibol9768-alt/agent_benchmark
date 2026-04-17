"""GLM (Zhipu / Z.ai) client via OpenAI-compatible SDK.

Uses the `openai` python package pointed at the vendor's OpenAI-compatible
endpoint. Both Zhipu (bigmodel.cn) and z.ai expose one. Env vars consulted
(first non-empty wins):

  GLM_API_KEY
  ZHIPUAI_API_KEY
  ZAI_API_KEY
  OPENAI_API_KEY          (last-resort fallback; assumes base_url override)

And:

  GLM_BASE_URL            default: https://open.bigmodel.cn/api/paas/v4/
  GLM_MODEL               default: glm-4.5

Model names known to support tool use:
  glm-4.5, glm-4.6, glm-4-plus, glm-4-long, glm-4-air, glm-4-flash
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Callable

from .retry import retrying_call
from .types import AgentLoopResult, ToolDef

log = logging.getLogger(__name__)


DEFAULT_BASE_URL = "https://open.bigmodel.cn/api/paas/v4/"
DEFAULT_MODEL = "glm-4.5"


def _resolve_key() -> str | None:
    for name in ("GLM_API_KEY", "ZHIPUAI_API_KEY", "ZAI_API_KEY", "OPENAI_API_KEY"):
        v = os.environ.get(name)
        if v:
            return v
    return None


class GLMClient:
    provider = "glm"

    def __init__(self, model: str | None = None, base_url: str | None = None, api_key: str | None = None):
        try:
            from openai import OpenAI
            import httpx
        except ImportError as e:
            raise RuntimeError("openai package not installed; pip install openai") from e

        self.model = model or os.environ.get("GLM_MODEL") or DEFAULT_MODEL
        self.base_url = base_url or os.environ.get("GLM_BASE_URL") or DEFAULT_BASE_URL
        self.api_key = api_key or _resolve_key()
        if not self.api_key:
            raise RuntimeError(
                "No GLM API key found. Set GLM_API_KEY (or ZHIPUAI_API_KEY / "
                "ZAI_API_KEY) to your Zhipu/Z.ai key. See "
                "https://open.bigmodel.cn/ or https://z.ai/ for credentials."
            )
        # trust_env=False so httpx ignores Windows/shell HTTP(S)_PROXY that
        # breaks direct connections to bigmodel.cn from within China.
        self._client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            http_client=httpx.Client(trust_env=False, timeout=120.0),
        )

    # ------------------------------------------------------------------

    def complete_text(self, *, system: str, user: str, max_tokens: int = 2000) -> str:
        resp = retrying_call(lambda: self._client.chat.completions.create(
            model=self.model,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        ))
        choice = resp.choices[0]
        return (choice.message.content or "").strip()

    # ------------------------------------------------------------------

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
        openai_tools = [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema,
                },
            }
            for t in tools
        ]

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

        result = AgentLoopResult()
        for turn in range(max_turns):
            try:
                resp = retrying_call(lambda: self._client.chat.completions.create(
                    model=self.model,
                    max_tokens=max_tokens,
                    tools=openai_tools,
                    tool_choice="auto",
                    messages=messages,
                ))
            except Exception as e:
                result.error = f"API error on turn {turn}: {e}"
                return result

            usage = getattr(resp, "usage", None)
            if usage is not None:
                result.input_tokens += getattr(usage, "prompt_tokens", 0) or 0
                result.output_tokens += getattr(usage, "completion_tokens", 0) or 0

            msg = resp.choices[0].message
            finish_reason = resp.choices[0].finish_reason
            result.turns += 1

            tool_calls = getattr(msg, "tool_calls", None) or []

            # Record assistant turn (with tool_calls) in OpenAI format.
            assistant_turn: dict[str, Any] = {
                "role": "assistant",
                "content": msg.content or "",
            }
            if tool_calls:
                assistant_turn["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments or "{}",
                        },
                    }
                    for tc in tool_calls
                ]
            messages.append(assistant_turn)

            if not tool_calls:
                result.final_text = msg.content or ""
                return result

            # Dispatch each tool call
            terminated = False
            for tc in tool_calls:
                name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}

                if name in terminal_tools:
                    result.terminal_tool_name = name
                    result.terminal_tool_args = args
                    result.tool_call_log.append((name, args, "(terminal)"))
                    # feed a final tool response so the model API is consistent,
                    # then break out of the loop
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
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
                result.tool_call_log.append((name, args, out[:500]))
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": out,
                })

            if terminated:
                return result
            if finish_reason == "stop":
                break

        return result
