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
