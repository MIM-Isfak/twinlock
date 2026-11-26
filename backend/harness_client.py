"""Translate the AgentCore wire protocol into TwinLock's local events."""

import json
import os
from collections.abc import Iterable, Iterator, Sequence
from contextlib import closing
from dataclasses import dataclass
from typing import Any, Protocol

from backend import config


@dataclass(frozen=True)
class Text:
    text: str


@dataclass(frozen=True)
class ToolUse:
    id: str
    name: str
    input: object


@dataclass(frozen=True)
class Stop:
    reason: str


@dataclass(frozen=True)
class ToolResult:
    id: str
    value: object


Event = Text | ToolUse | Stop


class HarnessError(RuntimeError):
    """A transport or protocol failure safe to report without the raw payload."""


class Harness(Protocol):
    def invoke(
        self, session_id: str, messages: Sequence[str], tool_results: Sequence[ToolResult],
    ) -> Iterator[Event]: ...


def _message_content(
    messages: Sequence[str], tool_results: Sequence[ToolResult],
) -> list[dict[str, Any]]:
    if bool(messages) == bool(tool_results):
        raise HarnessError("expected_user_messages_or_tool_results")
    if messages:
        return [{"text": message} for message in messages]
    # TODO(verify-in-smoke-test): confirm this documented callback works on the deployed harness.
    return [
        {"toolResult": {
            "toolUseId": result.id,
            "content": [{"text": json.dumps(result.value, allow_nan=False)}],
            "status": "success",
        }}
        for result in tool_results
    ]


@dataclass
class _ToolBlock:
    id: str
    name: str
    fragments: list[str]

    def finish(self) -> ToolUse:
        return ToolUse(self.id, self.name, json.loads("".join(self.fragments) or "{}"))


def _events(stream: Iterable[dict[str, Any]]) -> Iterator[Event]:
    # TODO(verify-in-smoke-test): confirm these documented event names, block boundaries,
    # and the final handoff stop reason against the installed SDK and live stream.
    open_tools: dict[int, _ToolBlock] = {}
    completed: dict[int, ToolUse] = {}
    pending: tuple[ToolUse, ...] = ()
    reason = ""
    for event in stream:
        if any(key.endswith(("Exception", "Error")) for key in event):
            raise HarnessError("harness_stream_failed")
        if "messageStart" in event:
            open_tools.clear()
            completed.clear()
            reason = ""
        elif "contentBlockStart" in event:
            block = event["contentBlockStart"]
            tool = block.get("start", {}).get("toolUse")
            if tool is not None and tool.get("type") in (None, "tool_use"):
                index = block["contentBlockIndex"]
                if index in open_tools or index in completed:
                    raise HarnessError("duplicate_tool_block")
                open_tools[index] = _ToolBlock(
                    tool["toolUseId"], tool["name"], [],
                )
        elif "contentBlockDelta" in event:
            block = event["contentBlockDelta"]
            delta = block["delta"]
            if "text" in delta:
                yield Text(delta["text"])
            tool_block = open_tools.get(block["contentBlockIndex"])
            if tool_block is not None and "toolUse" in delta:
                tool_block.fragments.append(delta["toolUse"]["input"])
        elif "contentBlockStop" in event:
            index = event["contentBlockStop"]["contentBlockIndex"]
            if index in open_tools:
                completed[index] = open_tools.pop(index).finish()
        elif "messageStop" in event:
            if open_tools:
                raise HarnessError("incomplete_tool_block")
            reason = event["messageStop"]["stopReason"]
            pending = tuple(completed[index] for index in sorted(completed))
    if not reason:
        raise HarnessError("missing_harness_stop")
    if reason == "tool_use":
        yield from pending
    yield Stop(reason)


class HarnessClient:
    def __init__(self) -> None:
        self._arn = os.environ.get("TWINLOCK_HARNESS_ARN", "").strip()
        if not self._arn:
            raise HarnessError("missing_TWINLOCK_HARNESS_ARN")
        try:
            import boto3  # Optional dependency for live mode only.
        except ImportError:
            raise HarnessError("boto3_required_for_live_mode") from None
        try:
            self._client = boto3.client("bedrock-agentcore", region_name=config.HARNESS_REGION)
            if not callable(getattr(self._client, "invoke_harness", None)):
                raise HarnessError("boto3_update_required_for_invoke_harness")
        except HarnessError:
            raise
        except Exception:
            raise HarnessError("harness_client_init_failed") from None

    def invoke(
        self, session_id: str, messages: Sequence[str], tool_results: Sequence[ToolResult],
    ) -> Iterator[Event]:
        try:
            response = self._client.invoke_harness(
                harnessArn=self._arn, runtimeSessionId=session_id,
                messages=[{"role": "user", "content": _message_content(messages, tool_results)}],
            )
            with closing(response["stream"]) as stream:
                yield from _events(stream)
        except HarnessError:
            raise
        except Exception:
            raise HarnessError("harness_invoke_failed") from None
