from __future__ import annotations

from http import HTTPStatus

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app import create_app
from core.errors import (
    AuthenticationError,
    ConflictError,
    DomainError,
    ForbiddenError,
    NotFoundError,
    UpstreamError,
    ValidationError,
)
from database import get_async_session
from middleware.common import domain_exception_handler, status_for_domain_error
from model.system_user.users import SystemUser
from schema.system_user.users import SystemUserRole
from service.system_user.users import create_system_user
from tests.conftest import auth_headers


@pytest.mark.asyncio
async def test_each_domain_error_maps_to_its_http_status_and_envelope() -> None:
    cases: tuple[tuple[DomainError, HTTPStatus], ...] = (
        (ValidationError("bad input"), HTTPStatus.BAD_REQUEST),
        (AuthenticationError("bad credentials"), HTTPStatus.UNAUTHORIZED),
        (ForbiddenError("role required"), HTTPStatus.FORBIDDEN),
        (NotFoundError("missing record"), HTTPStatus.NOT_FOUND),
        (ConflictError("duplicate record"), HTTPStatus.CONFLICT),
        (UpstreamError("daemon down"), HTTPStatus.BAD_GATEWAY),
    )

    probe = FastAPI()
    probe.add_exception_handler(DomainError, domain_exception_handler)

    for index, (error, _) in enumerate(cases):
        def make_route(to_raise: DomainError):
            async def route() -> None:
                raise to_raise

            return route

        probe.add_api_route(f"/case-{index}", make_route(error), methods=["GET"])

    async with AsyncClient(transport=ASGITransport(app=probe), base_url="http://test") as client:
        for index, (error, expected_status) in enumerate(cases):
            response = await client.get(f"/case-{index}")
            assert response.status_code == expected_status.value
            assert response.json() == {
                "code": expected_status.value,
                "message": str(error),
                "data": None,
            }


def test_unmapped_domain_error_subclass_fails_loudly() -> None:
    class UnregisteredError(DomainError):
        pass

    with pytest.raises(TypeError, match="Unmapped domain error type: UnregisteredError"):
        status_for_domain_error(UnregisteredError("oops"))


@pytest.mark.asyncio
async def test_services_surface_domain_errors_through_real_routes(test_db: None) -> None:
    admin = await create_system_user("admin", "secret-password", "admin@test.local", SystemUserRole.ADMIN)
    alice = await create_system_user("alice", "secret-password", "alice@test.local", SystemUserRole.USER)
    async with get_async_session() as session:
        admin_row = await session.get(SystemUser, admin.id)
        alice_row = await session.get(SystemUser, alice.id)
        assert admin_row is not None and alice_row is not None

    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as client:
        bad_login = await client.post(
            "/api/system-users/login",
            json={"email": "alice@test.local", "password": "wrong-password"},
        )
        assert bad_login.status_code == 401
        assert bad_login.json()["message"] == "Invalid email or password"

        no_token = await client.get("/api/work-projects")
        assert no_token.status_code == 401
        assert no_token.json()["message"] == "Missing access token"

        non_admin = await client.get("/api/system-users", headers=auth_headers(alice_row))
        assert non_admin.status_code == 403
        assert non_admin.json()["message"] == "Admin role required"

        assign_other_owner = await client.post(
            "/api/sandbox-containers",
            headers=auth_headers(alice_row),
            json={
                "host_id": 1,
                "image_id": 1,
                "egress_mode": "direct",
                "owner_id": admin_row.id,
                "port_mappings": [],
            },
        )
        assert assign_other_owner.status_code == 403
        assert assign_other_owner.json()["message"] == "No permission to assign the sandbox container owner"

        duplicate_user = await client.post(
            "/api/system-users",
            headers=auth_headers(admin_row),
            json={
                "username": "alice",
                "password": "secret-password",
                "email": "alice@test.local",
                "role": "user",
            },
        )
        assert duplicate_user.status_code == 409
        assert duplicate_user.json()["message"] == "Username or email already exists"

        demote_last_admin = await client.patch(
            f"/api/system-users/{admin_row.id}",
            headers=auth_headers(admin_row),
            json={"role": "user"},
        )
        assert demote_last_admin.status_code == 400
        assert demote_last_admin.json()["message"] == "The last administrator cannot be demoted"

        unknown_api = await client.get("/api/does-not-exist", headers=auth_headers(alice_row))
        assert unknown_api.status_code == 404
        assert unknown_api.json()["message"] == "Not found"
