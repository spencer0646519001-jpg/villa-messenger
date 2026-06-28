from dataclasses import dataclass


@dataclass(frozen=True)
class RoomPricingRule:
    room_count: int
    tier_key: str
    standard_capacity: int
    max_capacity: int
    extra_beds_allowed: bool


def resolve_room_pricing_rule(
    *, room_count: int, room_policy: dict, tenant_pricing: dict | None = None
) -> RoomPricingRule | None:
    rules = _room_rules(room_policy)
    same_room_rules = [r for r in rules if r.get("rooms_opened") == room_count]
    if not same_room_rules:
        return None

    standard_rule = _standard_rule(same_room_rules)
    if standard_rule is None:
        return None
    standard_capacity = _positive_int(standard_rule.get("max_people"))
    if standard_capacity is None:
        return None

    max_capacity = max(
        (_positive_int(r.get("max_people")) or 0 for r in same_room_rules),
        default=standard_capacity,
    )
    extra_beds_allowed = any(bool(r.get("extra_beds")) for r in same_room_rules)
    tier_key = f"{standard_capacity}_people"
    if tenant_pricing is not None and tier_key not in _base_prices(tenant_pricing):
        return None
    return RoomPricingRule(
        room_count=room_count,
        tier_key=tier_key,
        standard_capacity=standard_capacity,
        max_capacity=max_capacity,
        extra_beds_allowed=extra_beds_allowed,
    )


def minimum_rooms_for_guest_count(
    *, guest_count: int, room_policy: dict, tenant_pricing: dict | None = None
) -> int | None:
    candidates: list[int] = []
    for rule in _room_rules(room_policy):
        rooms = _positive_int(rule.get("rooms_opened"))
        max_people = _positive_int(rule.get("max_people"))
        min_people = _positive_int(rule.get("min_people")) or 1
        if rooms is None or max_people is None:
            continue
        if min_people <= guest_count <= max_people:
            pricing_rule = resolve_room_pricing_rule(
                room_count=rooms,
                room_policy=room_policy,
                tenant_pricing=tenant_pricing,
            )
            if pricing_rule is not None:
                candidates.append(rooms)
    return min(candidates) if candidates else None


def max_guest_capacity(room_policy: dict) -> int | None:
    configured = _positive_int(room_policy.get("max_capacity"))
    rule_max = max(
        (_positive_int(r.get("max_people")) or 0 for r in _room_rules(room_policy)),
        default=0,
    )
    return max(configured or 0, rule_max) or None


def _room_rules(room_policy: dict) -> list[dict]:
    rules = room_policy.get("room_opening_rules") or []
    return [r for r in rules if isinstance(r, dict)]


def _standard_rule(rules: list[dict]) -> dict | None:
    for rule in rules:
        if not rule.get("extra_beds"):
            return rule
    return None


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _base_prices(tenant_pricing: dict) -> dict:
    prices = tenant_pricing.get("base_prices_per_night") or {}
    return prices if isinstance(prices, dict) else {}
