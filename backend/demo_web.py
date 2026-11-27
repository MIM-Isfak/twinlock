"""Loopback-only browser demo backed by the TwinLock runner and simulator."""

import argparse
import json
import sqlite3
from dataclasses import dataclass, field
from functools import partial
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from secrets import token_urlsafe
from threading import Event as ThreadEvent, Lock, Thread

from backend import config, db
from backend.harness_client import Event, HarnessError, ToolResult
from backend.harness_fake import FakeHarnessClient, demo_steps
from backend.runner import ChatRunner, RunnerError, _maintain_heartbeat
from backend.runner_mock import DEMO_TARGETS, MockRunner

ROOT = Path(__file__).resolve().parents[1]
HOST = "127.0.0.1"
STRESS_CASES = ("injection", "authorization", "approval", "expiry", "replay", "tools")
ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}


def _saved_evidence() -> dict:
    def load(path: str) -> dict:
        return json.loads((ROOT / path).read_text(encoding="utf-8"))

    matrix = load("attacks/results.json")
    offline = load("docs/evidence/offline-pytest.json")
    smoke = load("docs/evidence/live-integration.json")
    cases = [load(f"docs/evidence/live-stress-{case}.json") for case in STRESS_CASES]
    return {
        "offlinePassed": offline["passed"],
        "offlineSubtests": offline["subtests_passed"],
        "offlineCells": sum(len(row) for row in matrix["results"].values()),
        "offlineUnexpected": len(matrix["unexpected_results"]),
        "livePassed": sum(row["mode"] == "live" and row["result"] == "PASS" for row in cases),
        "liveTotal": len(cases),
        "liveDate": smoke["run_date"],
        "approvedRun": smoke["mode"] == "live" and smoke["result"] == "PASS",
        "replayRefused": smoke["final_evidence"]["audit_log"][-1]["reason"] == "lease_consumed",
    }


def _summarize(event: Event | ToolResult, results: list[str]) -> None:
    # Only describe locally executed tools. Model prose and raw provider events stay server-side.
    if not isinstance(event, ToolResult):
        return
    if isinstance(event.value, list):
        device_ids = ", ".join(device["id"] for device in event.value)
        results.append(f"Authorized devices: {device_ids}." if device_ids else "No registered devices.")
        return
    if not isinstance(event.value, dict):
        return
    status = event.value.get("status")
    labels = {
        "pending_approval": "The lease is waiting for separate human approval.",
        "approved": "The simulator lease is ready to execute.",
        "executed": "The simulator accepted the leased action.",
        "refused": "The backend refused the request.",
        "inconclusive": "The backend could not verify the required state.",
    }
    if status in labels:
        message = labels[status]
        reasons = {
            "lease_consumed": " This lease has already been used.",
            "lease_expired": " The lease deadline has elapsed.",
            "device_not_found": " The device is outside this session's registry or does not exist.",
        }
        results.append(message + reasons.get(event.value.get("reason"), ""))
    elif "state_version" in event.value:
        results.append("The runner read the simulator state.")


@dataclass
class Lane:
    owner: str
    readers: str
    runner: ChatRunner
    chat: str
    messages: list[str]
    lock: Lock = field(default_factory=Lock)
    scripted_proposed: bool = False


class Demo:
    def __init__(self, store: db.Database, local: MockRunner, *, live: bool) -> None:
        self.store = store
        self.local = local
        self.live = live
        self.evidence = _saved_evidence()
        self.tokens: dict[str, str] = {user: token_urlsafe() for user in DEMO_TARGETS}
        self.lanes = {user: self._lane(user) for user in DEMO_TARGETS}

    def _lane(self, user: str) -> Lane:
        messages: list[str] = []
        observe = partial(_summarize, results=messages)
        runner = (ChatRunner.managed(self.local, observe=observe) if self.live else
                  ChatRunner(FakeHarnessClient(demo_steps(user)), self.local, observe=observe))
        return Lane(user, self.local.open_session(user), runner, runner.open_session(user), messages)

    def snapshot(self, lane: Lane) -> dict:
        devices = self.local.call(lane.readers, "list_devices")
        for device in devices:
            state = self.local.call(lane.readers, "get_device_state", device_id=device["id"])
            device["state"] = state.get("state")
        with self.store.transaction() as conn:
            lease = conn.execute(
                """SELECT lease_id, device_id, action, params, status, expires_at,
                          json_extract(manifest, '$.approval.ttl_s') AS ttl_s
                   FROM leases JOIN devices USING (user_id, device_id)
                   WHERE user_id = ? ORDER BY leases.rowid DESC LIMIT 1""", (lane.owner,),
            ).fetchone()
            events = conn.execute(
                """SELECT event, outcome, reason, occurred_at FROM audit_log
                   WHERE user_id = ? ORDER BY event_id DESC LIMIT ?""",
                (lane.owner, config.DEMO_WEB_AUDIT_ROWS),
            ).fetchall()
        return {"devices": devices, "lease": dict(lease) if lease else None,
                "offlineProposed": not self.live and lane.scripted_proposed,
                "audit": [dict(row) for row in events]}

    def send(self, lane: Lane, message: str) -> str:
        if not lane.lock.acquire(blocking=False):
            raise RequestError(HTTPStatus.CONFLICT, "A turn is already running.")
        try:
            expected = "Execute the existing lease" if lane.scripted_proposed else "Propose the demo action"
            if not self.live and message != expected:
                return f"Offline chat uses the guided script. Choose “{expected}”."
            lane.messages.clear()
            lane.runner.run_turn(lane.chat, message)
            lane.scripted_proposed = True
            return "\n".join(dict.fromkeys(lane.messages)) or "No verified tool result was returned."
        except (HarnessError, RunnerError) as exc:
            raise RequestError(HTTPStatus.BAD_GATEWAY, "The runner could not finish this turn. Check the audit before retrying.") from exc
        finally:
            lane.lock.release()


class RequestError(Exception):
    def __init__(self, status: HTTPStatus, message: str) -> None:
        self.status = status
        self.message = message


class DemoServer(ThreadingHTTPServer):
    daemon_threads = False

    def __init__(self, port: int, demo: Demo) -> None:
        self.demo = demo
        super().__init__((HOST, port), DemoHandler)
        self.authority = f"{HOST}:{self.server_port}"


class DemoHandler(BaseHTTPRequestHandler):
    server: DemoServer
    timeout = config.DEMO_WEB_REQUEST_TIMEOUT_S

    def log_message(self, *_: object) -> None:
        # Request paths, headers, chat, and payloads never enter an access log.
        return

    def _write(self, status: HTTPStatus, content: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy",
                         "default-src 'none'; script-src 'self'; style-src 'self'; "
                         "connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(content)

    def _json(self, value: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        self._write(status, json.dumps(value, allow_nan=False).encode(), "application/json")

    def _body(self, key: str) -> str:
        sizes = self.headers.get_all("Content-Length", [])
        try:
            length = int(sizes[0]) if len(sizes) == 1 else -1
            if (self.headers.get("Transfer-Encoding") is not None
                    or self.headers.get_content_type() != "application/json"
                    or not 0 < length <= config.DEMO_WEB_BODY_BYTES):
                raise ValueError()
            value = json.loads(self.rfile.read(length))
            if isinstance(value, dict) and set(value) == {key} and isinstance(value[key], str):
                cleaned = value[key].strip()
                if cleaned and cleaned.encode("utf-8"):
                    return cleaned
        except (ValueError, RecursionError):
            pass
        raise RequestError(HTTPStatus.BAD_REQUEST, "Check the request and try again.")

    def _lane(self) -> Lane:
        token = self.headers.get("X-TwinLock-Session")
        for user, expected in self.server.demo.tokens.items():
            if token == expected:
                return self.server.demo.lanes[user]
        raise RequestError(HTTPStatus.UNAUTHORIZED, "Select a demo identity to continue.")

    def _route(self, *, post: bool) -> None:
        demo = self.server.demo
        if self.headers.get_all("Host") != [self.server.authority]:
            raise RequestError(HTTPStatus.FORBIDDEN, "Open the printed loopback address.")
        if post and self.headers.get_all("Origin") != [f"http://{self.server.authority}"]:
            raise RequestError(HTTPStatus.FORBIDDEN, "The request must come from the local dashboard.")
        if not post and self.path in ASSETS:
            name, content_type = ASSETS[self.path]
            self._write(HTTPStatus.OK, (ROOT / "frontend" / name).read_bytes(), content_type)
        elif not post and self.path == "/api/info":
            self._json({"mode": "live" if demo.live else "offline", "evidence": demo.evidence,
                        "demoDuration": config.MOCK_ACTION_DURATION_S})
        elif post and self.path == "/api/session":
            user = self._body("identity")
            if user not in demo.tokens:
                raise RequestError(HTTPStatus.BAD_REQUEST, "Choose user1 or user2.")
            self._json({"session": demo.tokens[user]})
        elif not post and self.path == "/api/state":
            self._json(demo.snapshot(self._lane()))
        elif post and self.path == "/api/chat":
            lane = self._lane()
            self._json({"reply": demo.send(lane, self._body("message"))})
        else:
            raise RequestError(HTTPStatus.NOT_FOUND, "This route is unavailable.")

    def _handle(self, *, post: bool) -> None:
        try:
            self._route(post=post)
        except RequestError as exc:
            self._json({"error": exc.message}, exc.status)
        except (sqlite3.Error, OSError):
            self._json({"error": "The local demo could not complete the request."},
                       HTTPStatus.SERVICE_UNAVAILABLE)

    def do_GET(self) -> None:
        self._handle(post=False)

    def do_POST(self) -> None:
        self._handle(post=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--offline", action="store_true", help="Use the scripted offline harness (default).")
    mode.add_argument("--live", action="store_true", help="Opt into the existing AWS harness.")
    parser.add_argument("--port", type=int, default=config.DEMO_WEB_PORT)
    parser.add_argument("--db", type=Path, default=config.DEMO_WEB_DB_PATH)
    args = parser.parse_args()
    try:
        store = db.Database(args.db)
    except (OSError, sqlite3.Error):
        print("The demo could not open the local database.")
        return 1
    try:
        local = MockRunner(store)
        demo = Demo(store, local, live=args.live)
        stop = ThreadEvent()
        heartbeat = Thread(target=_maintain_heartbeat, args=(local, stop))
        with local.controller:
            heartbeat.start()
            try:
                with DemoServer(args.port, demo) as server:
                    print(f"TwinLock {'live' if args.live else 'offline'} demo at http://{server.authority}/", flush=True)
                    server.serve_forever()
            finally:
                stop.set()
                heartbeat.join()
    except KeyboardInterrupt:
        return 0
    except (HarnessError, OSError, ValueError, KeyError, sqlite3.Error):
        print("The demo could not start. Check the local port, evidence, and selected mode configuration.")
        return 1
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
