"""
Anthropic client wrapper — the one place in this service that talks to
Claude. Every specialist agent goes through this wrapper; none of them
import the Anthropic SDK directly. That keeps model selection, retry
behavior, and tool-use plumbing centralized and swappable in one place.

This is real, production-shippable integration code. It requires
ANTHROPIC_API_KEY to actually invoke Claude. In environments without a
configured key (e.g. this build sandbox), calls raise
AnthropicNotConfiguredError instead of silently faking a response —
agents and tests handle that explicitly rather than pretending a live
decision happened when it didn't.
"""
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import anthropic

from src.core import prompts
from src.core.config import get_settings


class AnthropicNotConfiguredError(RuntimeError):
    """Raised when a live Claude call is attempted without an API key configured."""


@dataclass
class ToolCallResult:
    tool_name: str
    tool_input: dict
    tool_output: Any


@dataclass
class AgentTurnResult:
    """What an agent gets back after reasoning to completion: the final
    text (if any), and the ordered list of tool calls it made along the
    way — this list becomes DecisionLog.tools_called verbatim."""

    final_text: str
    tool_calls: list[ToolCallResult] = field(default_factory=list)
    stop_reason: str = ""


class AnthropicAgentClient:
    def __init__(self) -> None:
        self._settings = get_settings()
        self._client: anthropic.Anthropic | None = None
        if self._settings.anthropic_api_key:
            self._client = anthropic.Anthropic(api_key=self._settings.anthropic_api_key)

    @property
    def is_configured(self) -> bool:
        return self._client is not None

    def run_agent_turn(
        self,
        system_prompt: str,
        user_message: str,
        tools: list[dict],
        tool_dispatch: dict[str, Callable[[dict], Any]],
        max_tool_iterations: int = 5,
    ) -> AgentTurnResult:
        """
        Runs a full agentic tool-use loop: send the message, execute any
        tool_use blocks Claude returns by dispatching to the matching
        Python function in tool_dispatch, feed the results back, and
        repeat until Claude stops calling tools or the iteration cap is
        hit. This is the real agent loop — not a single-shot completion.
        """
        if not self._client:
            raise AnthropicNotConfiguredError(
                "ANTHROPIC_API_KEY is not set for ai-orchestration-svc. "
                "This is real integration code; it requires live credentials to run."
            )

        messages: list[dict] = [{"role": "user", "content": user_message}]
        tool_calls: list[ToolCallResult] = []

        for _ in range(max_tool_iterations):
            response = self._client.messages.create(
                model=self._settings.anthropic_model,
                max_tokens=self._settings.anthropic_max_tokens,
                system=prompts.resolve(system_prompt),
                messages=messages,
                tools=tools,
            )

            if response.stop_reason != "tool_use":
                final_text = "".join(
                    block.text for block in response.content if block.type == "text"
                )
                return AgentTurnResult(
                    final_text=final_text,
                    tool_calls=tool_calls,
                    stop_reason=response.stop_reason,
                )

            messages.append({"role": "assistant", "content": response.content})
            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                handler = tool_dispatch.get(block.name)
                if handler is None:
                    output = {"error": f"no handler registered for tool '{block.name}'"}
                else:
                    output = handler(block.input)
                tool_calls.append(
                    ToolCallResult(tool_name=block.name, tool_input=block.input, tool_output=output)
                )
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": str(output),
                    }
                )
            messages.append({"role": "user", "content": tool_results})

        return AgentTurnResult(
            final_text="", tool_calls=tool_calls, stop_reason="max_iterations_reached"
        )
