from backend import config
from backend.db import Database
from backend.state import DeviceState, InMemoryStateProvider


def seed_demo(store: Database, state: InMemoryStateProvider, now: float) -> None:
    store.register_device({
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
    })
    store.register_device({
        "id": "front-door",
        "owner": "user1",
        "type": "door_lock",
        "actions": {"unlock": {"duration_s": {"max": config.DOOR_UNLOCK_MAX_S}}},
    })
    store.register_device({
        "id": "hall-light",
        "owner": "user2",
        "type": "switch",
        "actions": {"on": {}, "off": {}},
    })
    state.set_state("pump-1", DeviceState({"level_pct": 42}, now, 1))
    state.set_state("front-door", DeviceState({}, now, 1))
    state.set_state("hall-light", DeviceState({}, now, 1))
