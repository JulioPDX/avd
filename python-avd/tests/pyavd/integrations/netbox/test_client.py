# Copyright (c) 2023-2026 Arista Networks, Inc.
# Use of this source code is governed by the Apache License 2.0
# that can be found in the LICENSE file.
# ruff: noqa: ANN201, ANN001
"""Tests for NetBox HTTP client."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from pyavd.integrations.netbox.client import (
    NetBoxAPIError,
    NetBoxAuthError,
    NetBoxClient,
)


class TestNetBoxClientAuth:
    """Tests for NetBox client authentication."""

    def test_v2_token_header(self):
        """V2 tokens should use Bearer prefix."""
        client = NetBoxClient("http://netbox.local", "nbt_abc123.xyz789")
        header = client._auth_header
        assert header == {"Authorization": "Bearer nbt_abc123.xyz789"}

    def test_v1_token_header(self):
        """V1 tokens should use Token prefix."""
        client = NetBoxClient("http://netbox.local", "abc123xyz789")
        header = client._auth_header
        assert header == {"Authorization": "Token abc123xyz789"}

    def test_base_url_trailing_slash_removal(self):
        """Base URL should not have trailing slash."""
        client = NetBoxClient("http://netbox.local/", "token123")
        assert client.base_url == "http://netbox.local"


class TestNetBoxClientContextManager:
    """Tests for context manager functionality."""

    def test_enter_returns_self(self):
        """Context manager enter should return the client."""
        client = NetBoxClient("http://netbox.local", "token")
        assert client.__enter__() is client

    def test_exit_closes_client(self):
        """Context manager exit should close the client."""
        client = NetBoxClient("http://netbox.local", "token")
        # Force client creation first
        _ = client.client
        client.__exit__(None, None, None)
        # After exit, _client should be None
        assert client._client is None


class TestNetBoxClientMethods:
    """Tests for HTTP methods with mocked responses."""

    @pytest.fixture
    def mock_httpx_client(self):
        """Create a mock httpx.Client."""
        return MagicMock()

    @pytest.fixture
    def client(self, mock_httpx_client):
        """Create a client with mocked HTTP client."""
        client = NetBoxClient("http://netbox.local", "token")
        # Inject mock client
        client._client = mock_httpx_client
        return client

    def test_get_success(self, client, mock_httpx_client):
        """GET request should return JSON response."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b'{"id": 1}'
        mock_response.json.return_value = {"id": 1, "name": "test"}
        mock_httpx_client.get.return_value = mock_response

        result = client.get("/api/dcim/devices/")
        assert result == {"id": 1, "name": "test"}

    def test_get_with_params(self, client, mock_httpx_client):
        """GET request should pass query parameters."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b'{"results": []}'
        mock_response.json.return_value = {"results": []}
        mock_httpx_client.get.return_value = mock_response

        client.get("/api/dcim/devices/", params={"name": "spine1"})
        mock_httpx_client.get.assert_called_once()
        call_kwargs = mock_httpx_client.get.call_args[1]
        assert call_kwargs["params"] == {"name": "spine1"}

    def test_post_success(self, client, mock_httpx_client):
        """POST request should return created object."""
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.content = b'{"id": 1}'
        mock_response.json.return_value = {"id": 1, "name": "new-device"}
        mock_httpx_client.post.return_value = mock_response

        result = client.post("/api/dcim/devices/", {"name": "new-device"})
        assert result == {"id": 1, "name": "new-device"}

    def test_patch_success(self, client, mock_httpx_client):
        """PATCH request should return updated object."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b'{"id": 1}'
        mock_response.json.return_value = {"id": 1, "name": "updated"}
        mock_httpx_client.patch.return_value = mock_response

        result = client.patch("/api/dcim/devices/1/", {"name": "updated"})
        assert result == {"id": 1, "name": "updated"}

    def test_delete_success(self, client, mock_httpx_client):
        """DELETE request should succeed without error."""
        mock_response = MagicMock()
        mock_response.status_code = 204
        mock_httpx_client.delete.return_value = mock_response

        # Should not raise
        client.delete("/api/dcim/devices/1/")

    def test_401_raises_auth_error(self, client, mock_httpx_client):
        """401 response should raise NetBoxAuthError."""
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.content = b""
        mock_httpx_client.get.return_value = mock_response

        with pytest.raises(NetBoxAuthError):
            client.get("/api/dcim/devices/")

    def test_403_raises_auth_error(self, client, mock_httpx_client):
        """403 response should raise NetBoxAuthError."""
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.content = b""
        mock_httpx_client.get.return_value = mock_response

        with pytest.raises(NetBoxAuthError):
            client.get("/api/dcim/devices/")

    def test_4xx_raises_api_error(self, client, mock_httpx_client):
        """4xx response should raise NetBoxAPIError."""
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.content = b'{"error": "Bad Request"}'
        mock_response.json.return_value = {"error": "Bad Request"}
        mock_httpx_client.get.return_value = mock_response

        with pytest.raises(NetBoxAPIError):
            client.get("/api/dcim/devices/")

    def test_5xx_raises_api_error(self, client, mock_httpx_client):
        """5xx response should raise NetBoxAPIError."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.content = b"Internal Server Error"
        mock_response.json.side_effect = Exception("Not JSON")
        mock_response.text = "Internal Server Error"
        mock_httpx_client.get.return_value = mock_response

        with pytest.raises(NetBoxAPIError):
            client.get("/api/dcim/devices/")
