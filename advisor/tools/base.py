"""Tool abstraction.

Agents hold a ToolRegistry, never a transport. Two consequences the reviewer can
check: (1) importing httpx inside advisor/agents/ is a bug, (2) a tool can be
swapped for a fake in a test with no server involved.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from time import perf_counter
from typing import Any

from advisor.contracts import ToolResult
from advisor.logging_setup import log_event


class BaseTool(ABC):
    name: str = "tool"
    description: str = ""

    def spec(self) -> dict[str, Any]:
        """OpenAI-style function spec, used by the optional LLM tool-selection mode."""
        return {"type": "function",
                "function": {"name": self.name, "description": self.description,
                             "parameters": self.input_schema()}}

    def input_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    @abstractmethod
    def _run(self, **kwargs) -> ToolResult:
        """Real work. May raise - BaseTool.run() turns that into a ToolResult."""

    def run(self, **kwargs) -> ToolResult:
        """Never raises. Latency is always filled in, success or failure."""
        started = perf_counter()
        try:
            result = self._run(**kwargs)
        except Exception as exc:  # noqa: BLE001 - deliberate: failures are data
            result = ToolResult(ok=False, tool=self.name,
                                error=f"{type(exc).__name__}: {exc}")
        result.tool = self.name
        result.latency_ms = int((perf_counter() - started) * 1000)
        return result


class ToolRegistry:
    """Where every tool call is logged, so no call can go unlogged."""

    def __init__(self, tools: list[BaseTool] | None = None,
                 logger: logging.Logger | None = None):
        self._tools: dict[str, BaseTool] = {}
        self.logger = logger
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: BaseTool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool:
        if name not in self._tools:
            raise KeyError(f"unknown tool: {name}")
        return self._tools[name]

    def names(self) -> list[str]:
        return sorted(self._tools)

    def specs(self) -> list[dict[str, Any]]:
        return [t.spec() for t in self._tools.values()]

    def run(self, name: str, **kwargs) -> ToolResult:
        if self.logger:
            log_event(self.logger, "tool_call", agent="tools", tool=name, args=kwargs)
        result = self.get(name).run(**kwargs)
        if self.logger:
            level = "info" if result.ok else "warn"
            log_event(self.logger, "tool_result", agent="tools", tool=name, level=level,
                      status="ok" if result.ok else "error", latency_ms=result.latency_ms,
                      attempt=result.attempts, error=result.error,
                      degraded_reason=result.degraded_reason)
        return result
