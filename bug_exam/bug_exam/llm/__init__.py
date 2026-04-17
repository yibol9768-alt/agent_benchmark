"""Provider-agnostic LLM client abstraction.

The injector, scrubber, and solvers all talk to LLMs through this layer so
the rest of the codebase stays provider-neutral. Two providers are shipped
in Phase 2:

  - Anthropic (Claude Opus 4.6) via the `anthropic` SDK
  - GLM (Zhipu / Z.ai) via the `openai` SDK pointed at the vendor's
    OpenAI-compatible endpoint

Model and provider are picked via env vars or the solvers.yaml config. See
`factory.make_client()`.
"""
from .factory import make_client, resolve_provider
from .retry import retrying_call
from .types import AgentLoopResult, LLMClient, ToolDef

__all__ = ["make_client", "resolve_provider", "retrying_call", "AgentLoopResult", "LLMClient", "ToolDef"]
