# Copyright (c) 2023-2026 Arista Networks, Inc.
# Use of this source code is governed by the Apache License 2.0
# that can be found in the LICENSE file.
# ruff: noqa: ANN201, ANN001
"""Tests for NetBox client using httpx."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from pyavd.integrations.netbox.client import (
    NetBoxAPIError,
    NetBoxAuthError,
    NetBoxClient,
)


class TestNetBoxClientBasic:
    """Tests for NetBox client initialization."""

    def test_base_url_trailing_slash_removal(self):
        """Base URL should not have trailing slash."""
        client = NetBoxClient("http://netbox.local/", "token123")
        assert client.base_url == "http://netbox.local"
        client.close()

    def test_v1_token_auth_header(self):
        """V1 token should use Token auth header."""
        with patch("httpx.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client
            NetBoxClient("http://netbox.local", "abc123token")
            call_kwargs = mock_client_class.call_args[1]
            assert call_kwargs["headers"]["Authorization"] == "Token abc123token"

    def test_v2_token_auth_header(self):
        """V2 token (nbt_ prefix) should use Bearer auth header."""
        with patch("httpx.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client
            NetBoxClient("http://netbox.local", "nbt_v2token")
            call_kwargs = mock_client_class.call_args[1]
            assert call_kwargs["headers"]["Authorization"] == "Bearer nbt_v2token"


class TestNetBoxClientContextManager:
    """Tests for context manager functionality."""

    def test_enter_returns_self(self):
        """Context manager enter should return the client."""
        client = NetBoxClient("http://netbox.local", "token")
        assert client.__enter__() is client
        client.close()

    def test_exit_closes_client(self):
        """Context manager exit should close the httpx client."""
        client = NetBoxClient("http://netbox.local", "token")
        mock_httpx_client = MagicMock()
        client._client = mock_httpx_client
        client.__exit__(None, None, None)
        mock_httpx_client.close.assert_called_once()


class TestNetBoxClientMethods:
    """Tests for HTTP methods with mocked httpx."""

    @pytest.fixture
    def mock_httpx_client(self):
        """Create a mock httpx client."""
        return MagicMock()

    @pytest.fixture
    def client(self, mock_httpx_client):
        """Create a client with mocked httpx client."""
        client = NetBoxClient("http://netbox.local", "token")
        # Replace the internal httpx client with our mock
        client._client = mock_httpx_client
        return client

    def _make_mock_response(self, status_code: int, json_data: dict | None = None, text: str = "") -> MagicMock:
        """Create a mock httpx response."""
        mock_response = MagicMock()
        mock_response.status_code = status_code
        mock_response.reason_phrase = "OK" if status_code < 400 else "Error"
        mock_response.text = text or str(json_data)
        if json_data is not None:
            mock_response.json.return_value = json_data
        else:
            mock_response.json.side_effect = Exception("No JSON")
        return mock_response

    def test_get_success(self, client, mock_httpx_client):
        """GET request should return JSON response."""
        mock_response = self._make_mock_response(200, {"results": [{"id": 1, "name": "test"}], "count": 1})
        mock_httpx_client.get.return_value = mock_response

        result = client.get("/api/dcim/devices/")
        assert result["count"] == 1
        assert len(result["results"]) == 1
        assert result["results"][0]["id"] == 1

    def test_get_with_params(self, client, mock_httpx_client):
        """GET request should pass query parameters."""
        mock_response = self._make_mock_response(200, {"results": [], "count": 0})
        mock_httpx_client.get.return_value = mock_response

        client.get("/api/dcim/devices/", params={"name": "spine1"})
        mock_httpx_client.get.assert_called_once_with("/api/dcim/devices/", params={"name": "spine1"})

    def test_post_success(self, client, mock_httpx_client):
        """POST request should return created object."""
        mock_response = self._make_mock_response(201, {"id": 1, "name": "new-device"})
        mock_httpx_client.post.return_value = mock_response

        result = client.post("/api/dcim/devices/", {"name": "new-device"})
        assert result["id"] == 1
        assert result["name"] == "new-device"

    def test_patch_success(self, client, mock_httpx_client):
        """PATCH request should return updated object."""
        mock_response = self._make_mock_response(200, {"id": 1, "name": "updated"})
        mock_httpx_client.patch.return_value = mock_response

        result = client.patch("/api/dcim/devices/1/", {"name": "updated"})
        assert result["id"] == 1
        assert result["name"] == "updated"

    def test_delete_success(self, client, mock_httpx_client):
        """DELETE request should succeed without error."""
        mock_response = self._make_mock_response(204)
        mock_httpx_client.delete.return_value = mock_response

        # Should not raise
        client.delete("/api/dcim/devices/1/")
        mock_httpx_client.delete.assert_called_once_with("/api/dcim/devices/1/")

    def test_401_raises_auth_error(self, client, mock_httpx_client):
        """401 error should raise NetBoxAuthError."""
        mock_response = self._make_mock_response(401)
        mock_httpx_client.get.return_value = mock_response

        with pytest.raises(NetBoxAuthError):
            client.get("/api/dcim/devices/")

    def test_403_raises_auth_error(self, client, mock_httpx_client):
        """403 error should raise NetBoxAuthError."""
        mock_response = self._make_mock_response(403)
        mock_httpx_client.get.return_value = mock_response

        with pytest.raises(NetBoxAuthError):
            client.get("/api/dcim/devices/")

    def test_4xx_raises_api_error(self, client, mock_httpx_client):
        """400 error should raise NetBoxAPIError."""
        mock_response = self._make_mock_response(400, {"error": "Bad Request"})
        mock_httpx_client.get.return_value = mock_response

        with pytest.raises(NetBoxAPIError):
            client.get("/api/dcim/devices/")

    def test_get_all_pagination(self, client, mock_httpx_client):
        """get_all should handle pagination."""
        # First page
        page1 = self._make_mock_response(
            200,
            {
                "results": [{"id": 1}, {"id": 2}],
                "next": "http://netbox.local/api/dcim/devices/?offset=2",
                "count": 4,
            },
        )
        # Second page
        page2 = self._make_mock_response(
            200,
            {
                "results": [{"id": 3}, {"id": 4}],
                "next": None,
                "count": 4,
            },
        )
        mock_httpx_client.get.side_effect = [page1, page2]

        results = list(client.get_all("/api/dcim/devices/"))
        assert len(results) == 4
        assert [r["id"] for r in results] == [1, 2, 3, 4]
