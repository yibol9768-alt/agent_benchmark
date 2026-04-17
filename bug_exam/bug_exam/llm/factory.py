"""Factory that picks a provider based on env + config."""
from __future__ import annotations

import os

from .types import LLMClient


def resolve_provider(explicit: str | None = None) -> str:
    """Decide which provider to use.

    Precedence:
      1. Explicit argument
      2. BUG_EXAM_PROVIDER env var
      3. GLM key present  -> glm
      4. Anthropic key present -> anthropic
      5. Default to glm (will raise on first call if no key)
    """
    if explicit:
        return explicit.lower()
    env_pref = os.environ.get("BUG_EXAM_PROVIDER")
    if env_pref:
        return env_pref.lower()
    if any(os.environ.get(k) for k in ("GLM_API_KEY", "ZHIPUAI_API_KEY", "ZAI_API_KEY")):
        return "glm"
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return "anthropic"
    return "glm"


def make_client(provider: str | None = None, model: str | None = None, **kwargs) -> LLMClient:
    p = resolve_provider(provider)
    if p == "glm":
        from .glm_client import GLMClient
        return GLMClient(model=model, **kwargs)
    if p == "anthropic":
        from .anthropic_client import AnthropicClient
        return AnthropicClient(model=model, **kwargs)
    raise ValueError(f"unknown provider {p!r}")
