"""Offline, scripted implementation of the normalized harness protocol."""

from collections.abc import Callable, Iterable, Iterator, Sequence

from backend import config
from backend.harness_client import Event, HarnessError, Stop, Text, ToolResult, ToolUse

Reply = Callable[[Sequence[ToolResult]], Sequence[Event]]
Step = Sequence[Event] | Reply


class FakeHarnessClient:
    def __init__(self, steps: Iterable[Step]) -> None:
        self._steps = iter(steps)
        self._pending: dict[str, tuple[str, ...]] = {}
        self.invocations: list[tuple[str, tuple[str, ...], tuple[ToolResult, ...]]] = []

    def invoke(
        self, session_id: str, messages: Sequence[str], tool_results: Sequence[ToolResult],
    ) -> Iterator[Event]:
        ids = tuple(result.id for result in tool_results)
        if bool(messages) == bool(ids) or self._pending.get(session_id, ()) != ids:
            raise HarnessError("fake_handoff_mismatch")
        self.invocations.append((session_id, tuple(messages), tuple(tool_results)))
        step = next(self._steps, None)
        if step is None:
            raise HarnessError("fake_script_exhausted")
        events = step(tool_results) if callable(step) else step
        self._pending[session_id] = tuple(event.id for event in events if isinstance(event, ToolUse))
        yield from events


def demo_steps(user: str) -> Iterator[Step]:
    device = "pump-1" if user == "user1" else "hall-light"
    action = "run" if user == "user1" else "on"
    params = {"duration_s": config.MOCK_ACTION_DURATION_S} if user == "user1" else {}
    lease_id = None

    def proposed(results: Sequence[ToolResult]) -> Sequence[Event]:
        nonlocal lease_id
        result = results[0].value
        if isinstance(result, dict):
            lease_id = result.get("lease_id")
        return (Stop("end_turn"),)

    yield (ToolUse("list", "list_devices", {}), Stop("tool_use"))
    yield (ToolUse("state", "get_device_state", {"device_id": device}), Stop("tool_use"))
    yield (ToolUse("propose", "propose_action", {
        "device_id": device, "action": action, "params": params,
    }), Stop("tool_use"))
    yield proposed
    attempt = 0
    while lease_id:
        attempt += 1
        yield (ToolUse(f"execute-{attempt}", "execute_action", {"lease_id": lease_id}), Stop("tool_use"))
        yield (Stop("end_turn"),)
    while True:
        yield (Text("The scripted demonstration has no lease to execute."), Stop("end_turn"))
