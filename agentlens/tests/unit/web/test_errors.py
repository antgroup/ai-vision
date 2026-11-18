import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentlens.domain.errors import (
    Conflict,
    DomainError,
    ExternalUnavailable,
    Forbidden,
    InvalidTimeRange,
    NotFound,
    Validation,
)
from agentlens.web.errors import install_error_handlers


@pytest.fixture
def client():
    a = FastAPI()
    install_error_handlers(a)

    @a.get("/raise/{code}")
    def raise_(code: str):
        m = {
            "404": NotFound("nope"),
            "409": Conflict("dup"),
            "403": Forbidden("nope"),
            "400": InvalidTimeRange("bad range"),
            "422": Validation("bad"),
            "503": ExternalUnavailable("signoz down"),
            "500": DomainError("internal"),
        }
        raise m[code]

    return TestClient(a)


def test_mappings(client):
    cases = [
        ("404", 404),
        ("409", 409),
        ("403", 403),
        ("400", 400),
        ("422", 422),
        ("503", 503),
        ("500", 500),
    ]
    for path, expected in cases:
        r = client.get(f"/raise/{path}")
        assert r.status_code == expected
        body = r.json()
        assert "msg" in body
        assert "code" in body
