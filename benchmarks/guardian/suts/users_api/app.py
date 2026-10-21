"""8-operation Users API，行为由固定 mutation variant 控制。."""

from fastapi import FastAPI

from benchmarks.guardian.suts.common import RequestCounter, attach_benchmark_control


def create_app(variant: str) -> FastAPI:
    """为单个 case 创建干净、可计数的内存 SUT。."""
    app = FastAPI(title="Guardian Benchmark Users API", version="1.0.0")
    counter = RequestCounter()
    attach_benchmark_control(app, counter)

    @app.get("/users", response_model=None)
    async def list_users() -> list[dict[str, object]]:
        if variant == "user_list_id_type_lag":
            return [{"id": "1", "name": "Ada", "email": "ada@example.test"}]
        return [
            {"id": "1", "name": "Ada", "email": "ada@example.test"},
            {"id": "2", "name": "Lin", "email": "lin@example.test"},
        ]

    @app.get("/users/{user_id}", response_model=None)
    async def get_user(user_id: str) -> dict[str, object]:
        if variant == "user_email_missing":
            return {"id": 1, "name": "Ada"}
        if variant == "wrong_user_semantic":
            return {"id": 999, "name": "Wrong User", "email": "wrong@example.test"}
        if variant == "clean_control":
            return {"id": 1, "name": "Ada", "email": "ada@example.test"}
        if variant == "user_id_type_lag":
            return {"id": user_id, "name": "Ada", "email": "ada@example.test"}
        return {"id": user_id, "name": "Ada", "email": "ada@example.test"}

    @app.get("/profiles/{user_id}", response_model=None)
    async def get_profile(user_id: str) -> dict[str, object]:
        age: object = "42" if variant == "profile_age_type_lag" else "42"
        return {"user_id": user_id, "bio": "backend developer", "age": age}

    @app.get("/teams", response_model=None)
    async def list_teams() -> list[dict[str, object]]:
        if variant == "team_name_missing":
            return [{"id": 1}]
        return [{"id": "team-1", "name": "Platform"}]

    @app.get("/teams/{team_id}", response_model=None)
    async def get_team(team_id: str) -> dict[str, object]:
        if variant == "team_id_type_lag":
            return {"id": team_id, "name": "Platform"}
        return {"id": team_id, "name": "Platform"}

    @app.get("/roles", response_model=None)
    async def list_roles() -> list[dict[str, object]]:
        if variant == "role_description_missing":
            return [{"id": 1, "name": "reader"}]
        return [{"id": "role-1", "name": "reader"}]

    @app.get("/permissions", response_model=None)
    async def list_permissions() -> list[dict[str, object]]:
        if variant == "permission_id_type_lag":
            return [{"id": "permission-1", "name": "users:read"}]
        return [{"id": "permission-1", "name": "users:read"}]

    @app.get("/status", response_model=None)
    async def service_status() -> dict[str, object]:
        if variant == "status_region_semantic":
            return {"status": "ok", "region": 1}
        return {"status": "ok", "region": "local"}

    return app
