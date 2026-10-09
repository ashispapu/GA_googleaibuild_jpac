from .common import Contract, PlantId, Role, UserId


class TokenRequest(Contract):
    username: str
    password: str


class User(Contract):
    id: UserId
    name: str
    role: Role
    plant_ids: list[PlantId]
    permissions: list[str]


class TokenResponse(Contract):
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds
    user: User
