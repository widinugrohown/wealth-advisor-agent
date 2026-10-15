"""Agent abstraction: a name, one job, the tools it is allowed to use."""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from time import perf_counter

from advisor.contracts import RunContext
from advisor.logging_setup import log_event
from advisor.tools.base import ToolRegistry


class BaseAgent(ABC):
    name: str = "agent"

    def __init__(self, tools: ToolRegistry, logger: logging.Logger):
        self.tools = tools          # the ONLY way this agent reaches the outside world
        self.logger = logger

    @abstractmethod
    def run(self, ctx: RunContext) -> RunContext:
        """Read and write the shared context. Must not raise on tool failure."""

    def _log(self, event: str, **kw) -> None:
        log_event(self.logger, event, agent=self.name, **kw)

    def timed(self, ctx: RunContext, stage: str):
        """Context manager recording stage latency into the context."""
        agent, logger = self, self.logger

        class _Timer:
            def __enter__(self_inner):
                self_inner.start = perf_counter()
                agent._log("agent_start")
                return self_inner

            def __exit__(self_inner, *exc):
                elapsed = int((perf_counter() - self_inner.start) * 1000)
                ctx.stage_latency_ms[stage] = ctx.stage_latency_ms.get(stage, 0) + elapsed
                agent._log("agent_end", latency_ms=elapsed,
                           status="error" if exc and exc[0] else "ok")
                return False

        return _Timer()
