"""Exercise the loopback browser interface without contacting a provider."""

import http.client
import json
import subprocess
import sys
from contextlib import closing
from threading import Thread
from time import time

import pytest

from backend import config, db
from backend.demo_web import Demo, DemoServer, ROOT
from backend.harness_client import Stop, Text
from backend.harness_fake import FakeHarnessClient
from backend.runner import ChatRunner
from backend.runner_mock import MockRunner


@pytest.fixture
def browser(tmp_path, monkeypatch):
    database = tmp_path / "browser demo.db"
    monkeypatch.setattr(ChatRunner, "managed", lambda *_args, **_kwargs: pytest.fail("AWS adapter selected"))
    with closing(db.Database(database)) as store:
        local = MockRunner(store)
        with local.controller, DemoServer(0, Demo(store, local, live=False)) as server:
            worker = Thread(target=server.serve_forever)
            worker.start()

            def request(path, body=None, token=None, **overrides):
                headers = {"Origin": f"http://{server.authority}"}
                if token:
                    headers["X-TwinLock-Session"] = token
                if body is not None:
                    headers["Content-Type"] = "application/json"
                headers.update(overrides)
                method = "GET" if body is None else "POST"
                with closing(http.client.HTTPConnection("127.0.0.1", server.server_port)) as conn:
                    conn.request(method, path, json.dumps(body) if body is not None else None, headers)
                    response = conn.getresponse()
                    data = response.read()
                    return response.status, json.loads(data) if response.getheader(
                        "Content-Type") == "application/json" else data.decode()

            try:
                yield request, store, database
            finally:
                server.shutdown()
                worker.join()


def test_browser_serves_dashboard_and_preserved_evidence(browser):
    request, _, _ = browser
    assert request("/")[0] == request("/app.css")[0] == request("/app.js")[0] == 200
    assert "Software simulator" in request("/")[1]
    status, info = request("/api/info")
    assert status == 200 and info["mode"] == "offline"
    assert info["evidence"] == {
        "offlinePassed": 61, "offlineSubtests": 18,
        "offlineCells": 48, "offlineUnexpected": 0, "livePassed": 6, "liveTotal": 6,
        "liveDate": "2026-11-27", "approvedRun": True, "replayRefused": True,
    }


def test_browser_rejects_foreign_origin_and_unbound_requests(browser):
    request, _, _ = browser
    assert request("/api/session", {"identity": "user1"}, Origin="http://other.test")[0] == 403
    assert request("/api/info", Host="other.test")[0] == 403
    assert request("/api/state")[0] == 401
    assert request("/api/session", {"identity": "someone-else"})[0] == 400
    assert request("/api/chat", {"message": "Propose the demo action"})[0] == 401
    assert request("/api/approve", {"identity": "user1"})[0] == 404
    assert request("/docs/evidence/live-integration.json")[0] == 404


def test_browser_uses_cli_approval_and_refuses_replay(browser):
    request, store, database = browser
    user1 = request("/api/session", {"identity": "user1"})[1]["session"]
    user2 = request("/api/session", {"identity": "user2"})[1]["session"]
    assert user1 != user2
    state = lambda token: request("/api/state", token=token)[1]
    turn = lambda message: request("/api/chat", {"message": message}, user1)
    assert {row["id"] for row in state(user1)["devices"]} == {"front-door", "pump-1"}
    assert [row["id"] for row in state(user2)["devices"]] == ["hall-light"]
    assert request("/api/chat", {"message": "execute", "user_id": "user2"}, user1)[0] == 400
    assert "guided script" in turn("I approve")[1]["reply"]
    assert state(user1)["lease"] is None
    started = time()
    assert "waiting" in turn("Propose the demo action")[1]["reply"]
    lease = state(user1)["lease"]
    assert lease["status"] == "PENDING"
    assert lease["ttl_s"] == config.HIGH_LEASE_TTL_S
    assert started + config.HIGH_LEASE_TTL_S <= lease["expires_at"] <= time() + config.HIGH_LEASE_TTL_S
    assert "waiting" in turn("Execute the existing lease")[1]["reply"]
    assert state(user2)["lease"] is None and state(user2)["audit"] == []
    assert all(not device["state"].get("running") for device in state(user1)["devices"])
    approval = subprocess.run(
        [sys.executable, "-m", "approver.approve", "--user", "user1",
         "--db", str(database), "approve", lease["lease_id"]],
        cwd=ROOT, capture_output=True, text=True, timeout=config.HIGH_LEASE_TTL_S,
    )
    assert approval.returncode == 0 and approval.stdout.strip() == "approved"
    assert state(user1)["lease"]["status"] == "APPROVED"
    assert "accepted" in turn("Execute the existing lease")[1]["reply"]
    assert next(row for row in state(user1)["devices"] if row["id"] == "pump-1")["state"]["running"]
    assert "already been used" in turn("Execute the existing lease")[1]["reply"]
    final = state(user1)
    assert final["lease"]["status"] == "CONSUMED"
    assert final["audit"][0]["reason"] == "lease_consumed"
    with store.transaction() as conn:
        for table in ("run_ledger", "controller_receipts"):
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 1


def test_model_prose_is_never_relayed(tmp_path, monkeypatch):
    def fake_managed(local, *, observe):
        fake = FakeHarnessClient([(Text("Unverified provider content"), Stop("end_turn"))])
        return ChatRunner(fake, local, observe=observe)

    monkeypatch.setattr(ChatRunner, "managed", fake_managed)
    with closing(db.Database(tmp_path / "mock-live.db")) as store:
        demo = Demo(store, MockRunner(store), live=True)
        assert demo.send(demo.lanes["user1"], "Show state") == "No verified tool result was returned."
