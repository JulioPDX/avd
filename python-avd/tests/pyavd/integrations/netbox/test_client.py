# Copyright (c) 2023-2026 Arista Networks, Inc.
# Use of this source code is governed by the Apache License 2.0
# that can be found in the LICENSE file.
# ruff: noqa: ANN201, ANN001
"""Tests for NetBox client using pynetbox."""

from __future__ import annotations

from unittest.mock import MagicMock

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

    def test_token_stored(self):
        """Token should be stored on client."""
        test_token = "mytoken123"  # noqa: S105
        client = NetBoxClient("http://netbox.local", test_token)
        assert client.token == test_token


class TestNetBoxClientContextManager:
    """Tests for context manager functionality."""

    def test_enter_returns_self(self):
        """Context manager enter should return the client."""
        client = NetBoxClient("http://netbox.local", "token")
        assert client.__enter__() is client

    def test_exit_closes_client(self):
        """Context manager exit should close the client."""
        client = NetBoxClient("http://netbox.local", "token")
        # Set a mock api to verify it gets cleared
        client._api = MagicMock()
        client.__exit__(None, None, None)
        # After exit, _api should be None
        assert client._api is None


class TestNetBoxClientMethods:
    """Tests for HTTP methods with mocked pynetbox."""

    @pytest.fixture
    def mock_pynetbox_api(self):
        """Create a mock pynetbox API."""
        mock_api = MagicMock()
        # Mock the endpoint structure
        mock_api.dcim = MagicMock()
        mock_api.dcim.devices = MagicMock()
        mock_api.dcim.interfaces = MagicMock()
        mock_api.ipam = MagicMock()
        mock_api.ipam.vlans = MagicMock()
        return mock_api

    @pytest.fixture
    def client(self, mock_pynetbox_api):
        """Create a client with mocked pynetbox API."""
        client = NetBoxClient("http://netbox.local", "token")
        # Inject mock API
        client._api = mock_pynetbox_api
        return client

    def _make_mock_record(self, data: dict) -> MagicMock:
        """Create a mock pynetbox record that converts to dict properly."""
        mock_record = MagicMock()
        # Make dict() conversion work
        mock_record.keys.return_value = data.keys()
        mock_record.__iter__ = lambda _self: iter(data.keys())
        mock_record.__getitem__ = lambda _self, key: data[key]
        return mock_record

    def test_get_success(self, client, mock_pynetbox_api):
        """GET request should return results dict."""
        mock_record = self._make_mock_record({"id": 1, "name": "test"})
        mock_pynetbox_api.dcim.devices.filter.return_value = [mock_record]

        result = client.get("/api/dcim/devices/")
        assert result["count"] == 1
        assert len(result["results"]) == 1
        assert result["results"][0]["id"] == 1
        assert result["results"][0]["name"] == "test"

    def test_get_with_params(self, client, mock_pynetbox_api):
        """GET request should pass query parameters to filter."""
        mock_pynetbox_api.dcim.devices.filter.return_value = []

        client.get("/api/dcim/devices/", params={"name": "spine1"})
        mock_pynetbox_api.dcim.devices.filter.assert_called_once_with(name="spine1")

    def test_post_success(self, client, mock_pynetbox_api):
        """POST request should return created object."""
        mock_created = self._make_mock_record({"id": 1, "name": "new-device"})
        mock_pynetbox_api.dcim.devices.create.return_value = mock_created

        result = client.post("/api/dcim/devices/", {"name": "new-device"})
        assert result["id"] == 1
        assert result["name"] == "new-device"

    def test_patch_success(self, client, mock_pynetbox_api):
        """PATCH request should return updated object."""
        mock_obj = self._make_mock_record({"id": 1, "name": "updated"})
        mock_obj.save = MagicMock()
        mock_pynetbox_api.dcim.devices.get.return_value = mock_obj

        result = client.patch("/api/dcim/devices/1/", {"name": "updated"})
        mock_obj.save.assert_called_once()
        assert result["id"] == 1
        assert result["name"] == "updated"

    def test_delete_success(self, client, mock_pynetbox_api):
        """DELETE request should succeed without error."""
        mock_obj = MagicMock()
        mock_pynetbox_api.dcim.devices.get.return_value = mock_obj

        # Should not raise
        client.delete("/api/dcim/devices/1/")
        mock_obj.delete.assert_called_once()

    def _make_request_error(self, message: str) -> Exception:
        """Create a mock pynetbox RequestError."""
        from pyavd.integrations.netbox.client import _RequestError

        # Create a mock request object that RequestError needs
        mock_req = MagicMock()
        mock_req.status_code = int(message.split(maxsplit=1)[0]) if message.split(maxsplit=1)[0].isdigit() else 400
        mock_req.text = message

        # Create the error
        try:
            return _RequestError(mock_req)
        except (TypeError, AttributeError):
            # Fallback - create custom subclass
            err = _RequestError.__new__(_RequestError)
            Exception.__init__(err, message)
            return err

    def test_401_raises_auth_error(self, client, mock_pynetbox_api):
        """401 error should raise NetBoxAuthError."""
        error = self._make_request_error("401 Unauthorized - authentication required")
        mock_pynetbox_api.dcim.devices.filter.side_effect = error

        with pytest.raises(NetBoxAuthError):
            client.get("/api/dcim/devices/")

    def test_403_raises_auth_error(self, client, mock_pynetbox_api):
        """403 error should raise NetBoxAuthError."""
        error = self._make_request_error("403 Forbidden - insufficient permission")
        mock_pynetbox_api.dcim.devices.filter.side_effect = error

        with pytest.raises(NetBoxAuthError):
            client.get("/api/dcim/devices/")

    def test_4xx_raises_api_error(self, client, mock_pynetbox_api):
        """400 error should raise NetBoxAPIError."""
        error = self._make_request_error("400 Bad Request")
        mock_pynetbox_api.dcim.devices.filter.side_effect = error

        with pytest.raises(NetBoxAPIError):
            client.get("/api/dcim/devices/")

    def test_unknown_endpoint_raises_api_error(self, client):
        """Unknown endpoint should raise NetBoxAPIError."""
        with pytest.raises(NetBoxAPIError, match="Unknown endpoint"):
            client.get("/api/unknown/endpoint/")

    def test_patch_missing_id_raises_error(self, client):
        """PATCH without ID in endpoint should raise error."""
        with pytest.raises(NetBoxAPIError, match="Cannot extract ID"):
            client.patch("/api/dcim/devices/", {"name": "test"})
