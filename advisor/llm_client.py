"""The only door to an LLM.

Provider-agnostic by design: switching provider means changing LLM_BASE_URL /
LLM_API_KEY / LLM_MODEL in .env, nothing else. Same pattern as the RAG project.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

DEFAULT_BASE_URL = "https://ollama.com/v1"


class LLMUnavailable(RuntimeError):
    """Raised when the LLM is not configured - callers must have a fallback."""


@dataclass
class LLMResponse:
    text: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0


def is_configured() -> bool:
    return bool(os.getenv("LLM_API_KEY"))


def _client():
    from openai import OpenAI

    return OpenAI(base_url=os.getenv("LLM_BASE_URL", DEFAULT_BASE_URL),
                  api_key=os.getenv("LLM_API_KEY", ""),
                  timeout=float(os.getenv("LLM_TIMEOUT_S", "60")))


def chat(messages: list[dict[str, Any]], *, temperature: float = 0.0,
         response_format: dict | None = None, model: str | None = None,
         tools: list[dict] | None = None) -> LLMResponse:
    """One chat completion. Raises LLMUnavailable if no key is configured."""
    if not is_configured():
        raise LLMUnavailable("LLM_API_KEY is empty")
    kwargs: dict[str, Any] = {
        "model": model or os.getenv("LLM_MODEL", "gpt-oss:20b-cloud"),
        "messages": messages,
        "temperature": temperature,
    }
    if response_format:
        kwargs["response_format"] = response_format
    if tools:
        kwargs["tools"] = tools

    resp = _client().chat.completions.create(**kwargs)
    usage = getattr(resp, "usage", None)
    return LLMResponse(
        text=resp.choices[0].message.content or "",
        model=kwargs["model"],
        tokens_in=getattr(usage, "prompt_tokens", 0) or 0,
        tokens_out=getattr(usage, "completion_tokens", 0) or 0,
    )


def chat_json(messages: list[dict[str, Any]], **kwargs) -> tuple[dict | None, LLMResponse]:
    """Ask for JSON and tolerate a sloppy answer.

    Returns (parsed_or_None, response). None means the caller must fall back;
    it is deliberately not an exception so the pipeline keeps its status.
    """
    resp = chat(messages, response_format={"type": "json_object"}, **kwargs)
    text = resp.text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.split("\n", 1)[1] if "\n" in text else text
    try:
        return json.loads(text), resp
    except json.JSONDecodeError:
        return None, resp
