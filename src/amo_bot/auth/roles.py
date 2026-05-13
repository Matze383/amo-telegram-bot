from enum import StrEnum


class Role(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    VIP = "vip"
    NORMAL = "normal"
    IGNORE = "ignore"


ROLE_PRIORITY: dict[Role, int] = {
    Role.OWNER: 0,
    Role.ADMIN: 10,
    Role.VIP: 20,
    Role.NORMAL: 30,
    Role.IGNORE: 100,
}

# Permission-engine strength ranking for checks phrased as a minimum role.
# Higher is more privileged: owner > admin > vip > normal. ``ignore`` is
# intentionally outside the allow hierarchy and must be denied explicitly.
ROLE_ACCESS_RANK: dict[Role, int] = {
    Role.NORMAL: 0,
    Role.VIP: 10,
    Role.ADMIN: 20,
    Role.OWNER: 30,
}


def stricter_role(left: Role, right: Role) -> Role:
    """Return the stricter/higher minimum role from two allow-hierarchy roles."""

    return left if ROLE_ACCESS_RANK[left] >= ROLE_ACCESS_RANK[right] else right


def role_meets_minimum(role: Role, minimum: Role) -> bool:
    """True when ``role`` satisfies ``minimum`` in owner/admin/vip/normal order."""

    if role is Role.IGNORE:
        return False
    if role is Role.OWNER:
        return True
    return ROLE_ACCESS_RANK.get(role, -1) >= ROLE_ACCESS_RANK[minimum]
