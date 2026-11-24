from collections.abc import Mapping
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

from backend import config
from backend.state import DeviceState
from wizard.validator import SENSOR_OPERATORS, is_number


@dataclass(frozen=True)
class Decision:
    status: str
    reason: str | None = None


ALLOWED = Decision("allowed")


def check_proposal_rate(recent: int) -> Decision:
    if recent >= config.PROPOSALS_PER_MINUTE:
        return Decision("refused", "proposal_rate_exceeded")
    return ALLOWED


def check_pending_quota(pending: int) -> Decision:
    if pending >= config.PENDING_LEASES_PER_USER:
        return Decision("refused", "pending_lease_quota_exceeded")
    return ALLOWED


def check_action(
    user_id: str, device: Mapping[str, Any] | None, action: str, params: Mapping[str, object],
) -> Decision:
    if device is None or device["owner"] != user_id:
        return Decision("refused", "device_not_found")
    if not isinstance(action, str) or action in device["forbidden"] or action not in device["actions"]:
        return Decision("refused", "action_not_allowed")

    bounds_by_param = device["actions"][action]
    if not isinstance(params, Mapping) or set(params) != set(bounds_by_param):
        return Decision("refused", "invalid_parameters")
    for name, value in params.items():
        bounds = bounds_by_param[name]
        if not is_number(value) or not bounds["min"] <= value <= bounds["max"]:
            return Decision("refused", "parameter_out_of_bounds")
        if name == "duration_s":
            ceiling = config.ACTION_DURATION_CEILINGS.get(action)
            if value <= 0 or (ceiling is not None and value > ceiling):
                return Decision("refused", "parameter_out_of_bounds")
    return ALLOWED


def check_preconditions(
    conditions: list[dict[str, Any]], state: DeviceState | None, now: float,
) -> tuple[Decision, list[dict[str, Any]]]:
    if state is None:
        return Decision("inconclusive", "state_missing"), []
    if (not isinstance(state.values, Mapping) or not is_number(state.timestamp)
            or state.timestamp > now or type(state.version) is not int
            or not is_number(state.version) or state.version < 0):
        return Decision("inconclusive", "state_conflicting"), []

    evidence = []
    for condition in conditions:
        if now - state.timestamp > condition["max_age_s"]:
            return Decision("inconclusive", "state_stale"), []
        value = state.values.get(condition["sensor"])
        if value is None:
            return Decision("inconclusive", "sensor_missing"), []
        threshold = condition["value"]
        compatible = type(value) is bool if type(threshold) is bool else is_number(value)
        if not compatible:
            return Decision("inconclusive", "state_conflicting"), []
        if not SENSOR_OPERATORS[condition["op"]](value, threshold):
            return Decision("refused", "precondition_failed"), []
        evidence.append({"condition": condition, "value": value})
    return ALLOWED, evidence


def check_budget(
    budget: Mapping[str, Any] | None, params: Mapping[str, Any], used: Fraction,
) -> Decision:
    if budget is not None:
        requested = Fraction(str(params["duration_s"]))
        if used + requested > Fraction(str(budget["max_total_s"])):
            return Decision("refused", "budget_exceeded")
    return ALLOWED
