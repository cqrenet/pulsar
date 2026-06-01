import asyncio
from unittest.mock import patch

import auth
import pytest
from auth import _allowed, require_auth
from fastapi import HTTPException


@pytest.fixture(autouse=True)
def reset_cache():
    auth.JWKS_CACHE["keys"] = []
    auth.JWKS_CACHE["exp"] = 0


def test_allowed_no_restrictions():
    assert _allowed({}, set(), set()) is True


def test_allowed_by_role():
    assert _allowed({"roles": ["Admin"]}, {"Admin"}, set()) is True
    assert _allowed({"roles": ["User"]}, {"Admin"}, set()) is False


def test_allowed_by_group():
    assert _allowed({"groups": ["SecOps"]}, set(), {"SecOps"}) is True
    assert _allowed({"groups": ["Users"]}, set(), {"SecOps"}) is False


@patch("auth.AUTH_ENABLED", False)
def test_require_auth_disabled():
    claims = asyncio.run(require_auth(None))
    assert claims["sub"] == "anonymous"


@patch("auth.AUTH_ENABLED", True)
def test_require_auth_missing_header():
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(require_auth(None))
    assert exc_info.value.status_code == 401


@patch("auth.AUTH_ENABLED", True)
def test_require_auth_invalid_bearer():
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(require_auth("Basic abc"))
    assert exc_info.value.status_code == 401
