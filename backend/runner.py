"""Run the managed TwinLock chat or the offline scripted demonstration."""

import argparse
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from threading import Event as ThreadEvent, Thread

from backend import config, db
from backend.engine import Result
from backend.harness_client import Event, Harness, HarnessClient, HarnessError, Stop, Text, ToolResult, ToolUse
from backend.harness_fake import FakeHarnessClient, demo_steps
from backend.runner_mock import DEMO_TARGETS, MockRunner
from backend.tools import TOOL_ARGUMENTS


@dataclass
class Session:
    turns: int = 0
    seen_call_ids: set[str] = field(default_factory=set)


class RunnerError(RuntimeError):
    """A local refusal with a stable, user-facing reason code."""


Observer = Callable[[Event | ToolResult], None]


def _contains_identity(value: object) -> bool:
    if isinstance(value, dict):
        return "user_id" in value or any(_contains_identity(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_identity(item) for item in value)
    return False


def _tool_arguments(call: ToolUse) -> dict[str, object]:
    if call.name not in TOOL_ARGUMENTS:
        raise RunnerError("unknown_tool")
    arguments = call.input
    if not isinstance(arguments, dict) or set(arguments) != set(TOOL_ARGUMENTS[call.name]):
        raise RunnerError("invalid_tool_arguments")
    if _contains_identity(arguments):
        raise RunnerError("invalid_tool_arguments")
    return arguments


class ChatRunner:
    def __init__(self, harness: Harness, local: MockRunner, *, observe: Observer | None = None) -> None:
        self._harness = harness
        self._local = local
        self._observe = observe
        self._sessions: dict[str, Session] = {}

    @classmethod
    def managed(cls, local: MockRunner, *, observe: Observer | None = None) -> "ChatRunner":
        return cls(HarnessClient(), local, observe=observe)

    def open_session(self, authenticated_user: str) -> str:
        # The local dispatcher binds the authenticated user to this UUID for every handler call.
        session_id = self._local.open_session(authenticated_user)
        self._sessions[session_id] = Session()
        return session_id

    def run_turn(self, session_id: str, message: str) -> str:
        session = self._sessions.get(session_id)
        if session is None:
            raise RunnerError("unknown_session")
        if session.turns >= config.CHAT_TURNS_PER_SESSION:
            raise RunnerError("chat_turn_limit")
        session.turns += 1
        try:
            return self._run_turn(session_id, session, message)
        except (HarnessError, RunnerError):
            # A failed handoff may leave pending calls in the remote session.
            del self._sessions[session_id]
            raise

    def _run_turn(self, session_id: str, session: Session, message: str) -> str:
        messages: tuple[str, ...] = (message,)
        results: list[ToolResult] = []
        calls_used = 0
        notices: dict[str, str] = {}
        refusal = ""
        while True:
            try:
                events = list(self._harness.invoke(session_id, messages, results))
            except HarnessError:
                if not refusal:
                    raise
                del self._sessions[session_id]
                return refusal
            if self._observe is not None:
                for event in events:
                    self._observe(event)
            calls = [event for event in events if isinstance(event, ToolUse)]
            stops = [event for event in events if isinstance(event, Stop)]
            valid_stop = len(stops) == 1 and events[-1] == stops[0]
            if refusal:
                if not valid_stop or stops[0].reason != "end_turn" or calls:
                    del self._sessions[session_id]
                return refusal
            if not valid_stop:
                raise RunnerError("invalid_harness_stop")
            reason = stops[0].reason
            if reason != "tool_use":
                if calls:
                    raise RunnerError("invalid_tool_handoff")
                if reason != "end_turn":
                    return "\n".join([*notices.values(), "The harness stopped before completing the turn."])
                text = "".join(event.text for event in events if isinstance(event, Text))
                return "\n".join(notices.values()) if notices else text
            if not calls:
                raise RunnerError("missing_tool_call")
            calls_used += len(calls)
            if calls_used > config.TOOL_CALLS_PER_TURN:
                raise RunnerError("tool_call_limit")
            ids = {call.id for call in calls}
            if "" in ids or len(ids) != len(calls) or ids & session.seen_call_ids:
                raise RunnerError("duplicate_or_empty_tool_call_id")
            arguments = [_tool_arguments(call) for call in calls]
            session.seen_call_ids.update(ids)
            messages = ()
            results = []
            for call, args in zip(calls, arguments, strict=True):
                value = self._dispatch(session_id, call.name, args)
                result = ToolResult(call.id, value)
                results.append(result)
                if self._observe is not None:
                    self._observe(result)
                status = value.get("status") if isinstance(value, dict) else None
                if status in ("refused", "inconclusive"):
                    refusal = f"{status}: {value['reason']}"
                    break
                if status == "pending_approval":
                    lease_id = value["lease_id"]
                    notices[lease_id] = f"Pending human approval for lease {lease_id}."
                if call.name == "propose_action" and status == "approved":
                    lease_id = value["lease_id"]
                    notices[lease_id] = f"Lease {lease_id} is ready to execute."
                if call.name == "execute_action" and status == "executed":
                    lease_id = value["lease_id"]
                    notices[lease_id] = f"Executed lease {lease_id}."

    def _dispatch(self, session_id: str, name: str, args: dict[str, object]) -> object:
        if name == "execute_action":
            self._local.controller.heartbeat()
        value = self._local.call(session_id, name, **args)
        return asdict(value) if isinstance(value, Result) else value


def _chat(runner: ChatRunner, user: str, read: Callable[[str], str] = input) -> int:
    session = runner.open_session(user)
    for _ in range(config.CHAT_TURNS_PER_SESSION):
        try:
            message = read("You> ").strip()
        except (EOFError, KeyboardInterrupt):
            return 0
        if message in ("", "/quit"):
            return 0
        print(runner.run_turn(session, message))
        if session not in runner._sessions:
            return 1
    print("chat_turn_limit")
    return 0


def _maintain_heartbeat(local: MockRunner, stop: ThreadEvent) -> None:
    while not stop.wait(config.CONTROLLER_POLL_INTERVAL_S):
        local.controller.heartbeat()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", required=True, choices=DEMO_TARGETS)
    parser.add_argument("--fake", action="store_true")
    parser.add_argument("--db", default=config.MOCK_DB_PATH)
    args = parser.parse_args(argv)
    try:
        harness = FakeHarnessClient(demo_steps(args.user)) if args.fake else HarnessClient()
        store = db.Database(args.db)
        try:
            local = MockRunner(store)
            stop = ThreadEvent()
            heartbeat = Thread(target=_maintain_heartbeat, args=(local, stop), daemon=True)
            with local.controller:
                heartbeat.start()
                try:
                    return _chat(ChatRunner(harness, local), args.user)
                finally:
                    stop.set()
                    heartbeat.join()
        finally:
            store.close()
    except (HarnessError, RunnerError) as exc:
        print(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
