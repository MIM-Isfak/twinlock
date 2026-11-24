from backend import config
from backend.db import Database, get_device
from backend.state import DeviceState, InMemoryStateProvider


def register_demo_devices(store: Database) -> None:
    manifests = ({
        "id": "pump-1",
        "owner": "user1",
        "type": "pump",
        "actions": {"run": {"duration_s": {"max": config.PUMP_RUN_MAX_S}}},
        "preconditions": [{
            "sensor": "level_pct", "op": "<", "value": config.DEMO_PUMP_LEVEL_LIMIT,
            "max_age_s": config.DEMO_PUMP_MAX_AGE_S,
        }],
        "budget": {
            "window_s": config.DEMO_PUMP_BUDGET_WINDOW_S,
            "max_total_s": config.DEMO_PUMP_BUDGET_TOTAL_S,
        },
    }, {
        "id": "front-door",
        "owner": "user1",
        "type": "door_lock",
        "actions": {"unlock": {"duration_s": {"max": config.DOOR_UNLOCK_MAX_S}}},
    }, {
        "id": "hall-light",
        "owner": "user2",
        "type": "switch",
        "actions": {"on": {}, "off": {}},
    })
    for manifest in manifests:
        with store.transaction() as conn:
            existing = get_device(conn, manifest["owner"], manifest["id"])
        if existing is None:
            store.register_device(manifest)


def seed_demo(store: Database, state: InMemoryStateProvider, now: float) -> None:
    register_demo_devices(store)
    state.set_state("pump-1", DeviceState({"level_pct": config.TANK_INITIAL_LEVEL_PCT}, now, 1))
    state.set_state("front-door", DeviceState({}, now, 1))
    state.set_state("hall-light", DeviceState({}, now, 1))
