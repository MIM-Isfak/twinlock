from backend import config
from tests.support import EngineTestCase
from wizard.validator import ManifestError


class TestTenancy(EngineTestCase):
    def test_user2_cannot_target_user1_device_or_read_lease(self) -> None:
        result = self.engine.propose("user2", "front-door", "unlock", {"duration_s": 5})
        assert result.reason == "device_not_found"
        lease_id = self.approved_pump()
        result = self.engine.execute("user2", lease_id)
        assert result.reason == "lease_not_found" and result.lease_id is None
        assert self.engine.execute("user1", lease_id).status == "executed"
        with self.store.transaction() as conn:
            attempts = conn.execute(
                """SELECT event, reason, lease_id FROM audit_log
                   WHERE user_id = ? ORDER BY event_id""", ("user2",),
            )
            assert [tuple(row) for row in attempts] == [
                ("propose", "device_not_found", None), ("execute", "lease_not_found", None),
            ]

    def test_device_quota_per_user(self) -> None:
        manifest = {"owner": "user1", "type": "switch", "actions": {"on": {}}}
        for index in range(config.DEVICES_PER_USER - 2):
            self.store.register_device({**manifest, "id": f"switch-{index}"})
        with self.assertRaisesRegex(ManifestError, "device_quota_exceeded"):
            self.store.register_device({**manifest, "id": "overflow"})
        self.store.register_device({**manifest, "id": "overflow", "owner": "user2"})
