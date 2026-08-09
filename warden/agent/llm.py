"""LLM backend abstraction with a deterministic fallback.

Warden must run end to end with no model reachable. A missing API key
degrades judgment quality; it never breaks the pipeline. The deterministic
fallback also means CI can exercise every path with no network.
"""

import json
import logging
from typing import Protocol

import httpx

from warden.agent.config import settings

logger = logging.getLogger(__name__)

_OLLAMA_URL = "http://localhost:11434/api/generate"
_TIMEOUT = 60.0


class Reasoner(Protocol):
    def judge(self, prompt: str) -> str: ...


class DeterministicReasoner:
    """Used when no backend is reachable. Returns a refusal-shaped answer
    rather than a guess — consistent with Warden declining rather than
    asserting under uncertainty."""

    def judge(self, prompt: str) -> str:
        return json.dumps({"decision": "unknown", "reason": "no llm backend available"})


class OllamaReasoner:
    def __init__(self, model: str = "llama3.2") -> None:
        self.model = model

    def judge(self, prompt: str) -> str:
        response = httpx.post(
            _OLLAMA_URL,
            json={"model": self.model, "prompt": prompt, "stream": False},
            timeout=_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()["response"]


class AnthropicReasoner:
    def __init__(self, model: str = "claude-sonnet-4-6") -> None:
        import anthropic

        self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        self.model = model

    def judge(self, prompt: str) -> str:
        message = self._client.messages.create(
            model=self.model,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text


def get_reasoner() -> Reasoner:
    backend = settings.llm_backend.lower()
    try:
        if backend == "anthropic" and settings.anthropic_api_key:
            return AnthropicReasoner()
        if backend == "ollama":
            httpx.get("http://localhost:11434/api/tags", timeout=2.0)
            return OllamaReasoner()
    except Exception as exc:
        logger.warning("llm backend %s unreachable (%s); using deterministic", backend, exc)
    return DeterministicReasoner()
