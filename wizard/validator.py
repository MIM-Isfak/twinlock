import math
import operator
import unicodedata
from collections.abc import Mapping
from typing import Any, TypeGuard

from backend import config

SENSOR_OPERATORS = {
    "<": operator.lt, "<=": operator.le, "==": operator.eq, "!=": operator.ne,
    ">=": operator.ge, ">": operator.gt,
}
_RISKS = ("low", "high", "critical")
_MANIFEST_FIELDS = {
    "id", "owner", "type", "risk", "actions", "preconditions", "budget",
    "approval", "forbidden", *config.FREE_TEXT_LENGTHS,
}


class ManifestError(ValueError):
    pass


def is_number(value: object) -> TypeGuard[int | float]:
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _positive(value: object) -> TypeGuard[int | float]:
    return is_number(value) and value > 0


def is_identifier(value: object) -> TypeGuard[str]:
    return (isinstance(value, str) and bool(value) and value == value.strip()
            and all(not unicodedata.category(char).startswith("C") for char in value))


def _parameter_bounds(action: str, name: str, bounds: object) -> dict[str, int | float]:
    if not isinstance(bounds, dict) or set(bounds) - {"min", "max"}:
        raise ManifestError("invalid_parameter_bounds")
    minimum = bounds.get("min", 0)
    maximum = bounds.get("max")
    if not is_number(minimum) or not is_number(maximum) or minimum > maximum:
        raise ManifestError("invalid_parameter_bounds")
    if name == "duration_s":
        if minimum < 0 or maximum <= 0:
            raise ManifestError("invalid_duration_bounds")
        ceiling = config.ACTION_DURATION_CEILINGS.get(action)
        if ceiling is not None and maximum > ceiling:
            raise ManifestError("duration_above_platform_ceiling")
    return {"min": minimum, "max": maximum}


def _actions(value: object, device_type: str) -> dict[str, dict[str, dict[str, int | float]]]:
    if not isinstance(value, dict) or not value:
        raise ManifestError("invalid_actions")
    allowed = config.DEVICE_ACTIONS.get(device_type)
    actions = {}
    for action, parameters in value.items():
        if not is_identifier(action) or (allowed is not None and action not in allowed):
            raise ManifestError("invalid_action")
        if not isinstance(parameters, dict) or any(not is_identifier(key) for key in parameters):
            raise ManifestError("invalid_parameters")
        if action in config.ACTION_DURATION_CEILINGS and "duration_s" not in parameters:
            raise ManifestError("duration_required")
        actions[action] = {
            name: _parameter_bounds(action, name, bounds) for name, bounds in parameters.items()
        }
    return actions


def _preconditions(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ManifestError("invalid_preconditions")
    conditions = []
    for condition in value:
        if not isinstance(condition, dict) or set(condition) != {"sensor", "op", "value", "max_age_s"}:
            raise ManifestError("invalid_precondition")
        op = condition["op"]
        threshold = condition["value"]
        if (not is_identifier(condition["sensor"]) or not isinstance(op, str)
                or op not in SENSOR_OPERATORS or not _positive(condition["max_age_s"])):
            raise ManifestError("invalid_precondition")
        if not is_number(threshold) and not (type(threshold) is bool and op in {"==", "!="}):
            raise ManifestError("invalid_precondition_value")
        conditions.append(dict(condition))
    return conditions


def _approval(value: object, risk: str) -> dict[str, Any]:
    ceiling = config.LEASE_TTL_S[risk]
    if not isinstance(value, dict) or set(value) - {"required", "ttl_s"}:
        raise ManifestError("invalid_approval")
    required = value.get("required", risk != "low")
    ttl = value.get("ttl_s", ceiling)
    if type(required) is not bool or (risk != "low" and not required):
        raise ManifestError("approval_required_for_risk")
    if not _positive(ttl) or ttl > ceiling:
        raise ManifestError("invalid_lease_ttl")
    return {"required": required, "ttl_s": ttl}


def _budget(value: object, actions: Mapping[str, Mapping]) -> dict[str, Any] | None:
    if value is None:
        return None
    if (not isinstance(value, dict) or set(value) != {"window_s", "max_total_s"}
            or not all(_positive(limit) for limit in value.values())
            or any("duration_s" not in params for params in actions.values())):
        raise ManifestError("invalid_budget")
    return dict(value)


def validate_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and return a detached, normalized manifest."""
    if not isinstance(manifest, Mapping) or set(manifest) - _MANIFEST_FIELDS:
        raise ManifestError("invalid_manifest_fields")
    identifiers = {field: manifest.get(field) for field in ("id", "owner", "type")}
    if not all(is_identifier(value) for value in identifiers.values()):
        raise ManifestError("invalid_device_identity")
    device_type = identifiers["type"]
    floor = config.DEVICE_RISK_FLOORS.get(device_type, "high")
    risk = manifest.get("risk", floor)
    if risk not in _RISKS or _RISKS.index(risk) < _RISKS.index(floor):
        raise ManifestError("risk_below_platform_floor")
    actions = _actions(manifest.get("actions"), device_type)
    forbidden = manifest.get("forbidden", [])
    if not isinstance(forbidden, list) or any(not is_identifier(action) for action in forbidden):
        raise ManifestError("invalid_forbidden_actions")

    result = {
        **identifiers, "risk": risk, "actions": actions, "forbidden": list(forbidden),
        "preconditions": _preconditions(manifest.get("preconditions", [])),
        "budget": _budget(manifest.get("budget"), actions),
        "approval": _approval(manifest.get("approval", {}), risk),
    }
    for field, limit in config.FREE_TEXT_LENGTHS.items():
        if field in manifest:
            value = manifest[field]
            if not isinstance(value, str):
                raise ManifestError("invalid_free_text")
            result[field] = "".join(
                char for char in value if not unicodedata.category(char).startswith("C")
            )[:limit]
    return result
