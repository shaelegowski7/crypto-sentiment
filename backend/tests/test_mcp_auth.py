"""API-key extraction for the MCP server.

The Claude API's MCP connector can only send `Authorization: Bearer <token>`
(its `mcp_servers` entry has no custom-header field), while every other client
is told to send `X-API-Key`. Both must resolve to the same key, or Claude API
apps can't reach /mcp at all.
"""
from starlette.datastructures import Headers

from app.mcp_server import _api_key_from_headers


def test_x_api_key_header():
    assert _api_key_from_headers(Headers({"X-API-Key": "sfx_abc"})) == "sfx_abc"


def test_bearer_token():
    assert _api_key_from_headers(Headers({"Authorization": "Bearer sfx_abc"})) == "sfx_abc"


def test_bearer_scheme_is_case_insensitive():
    assert _api_key_from_headers(Headers({"authorization": "bearer sfx_abc"})) == "sfx_abc"


def test_x_api_key_wins_over_bearer():
    headers = Headers({"X-API-Key": "sfx_header", "Authorization": "Bearer sfx_bearer"})
    assert _api_key_from_headers(headers) == "sfx_header"


def test_non_bearer_authorization_is_ignored():
    assert _api_key_from_headers(Headers({"Authorization": "Basic dXNlcjpwYXNz"})) is None


def test_empty_bearer_is_ignored():
    assert _api_key_from_headers(Headers({"Authorization": "Bearer "})) is None


def test_no_key():
    assert _api_key_from_headers(Headers({})) is None
