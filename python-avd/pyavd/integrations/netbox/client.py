# Copyright (c) 2023-2026 Arista Networks, Inc.
# Use of this source code is governed by the Apache License 2.0
# that can be found in the LICENSE file.
"""
NetBox API Client.

Wrapper around pynetbox for interacting with NetBox REST API.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator

    from typing_extensions import Self

# pynetbox is an optional dependency - only imported at runtime when NetBox integration is used
_PYNETBOX_INSTALLED = False
_pynetbox: Any = None
_RequestError: type[Exception] = Exception

try:
    import pynetbox as _pynetbox_module  # pyright: ignore[reportMissingImports]

    _pynetbox = _pynetbox_module
    _RequestError = _pynetbox_module.core.query.RequestError
    _PYNETBOX_INSTALLED = True
except ImportError:
    pass

LOGGER = logging.getLogger(__name__)

# Map endpoint paths to pynetbox API paths
ENDPOINT_MAP: dict[str, tuple[str, str]] = {
    "/api/dcim/devices/": ("dcim", "devices"),
    "/api/dcim/interfaces/": ("dcim", "interfaces"),
    "/api/dcim/sites/": ("dcim", "sites"),
    "/api/dcim/device-roles/": ("dcim", "device_roles"),
    "/api/dcim/device-types/": ("dcim", "device_types"),
    "/api/dcim/manufacturers/": ("dcim", "manufacturers"),
    "/api/dcim/platforms/": ("dcim", "platforms"),
    "/api/dcim/cables/": ("dcim", "cables"),
    "/api/ipam/ip-addresses/": ("ipam", "ip_addresses"),
    "/api/ipam/vlans/": ("ipam", "vlans"),
    "/api/ipam/vrfs/": ("ipam", "vrfs"),
    "/api/ipam/prefixes/": ("ipam", "prefixes"),
    "/api/ipam/asns/": ("ipam", "asns"),
    "/api/ipam/rirs/": ("ipam", "rirs"),
}


class NetBoxClientError(Exception):
    """Base exception for NetBox client errors."""


class NetBoxAuthError(NetBoxClientError):
    """Authentication error with NetBox."""


class NetBoxAPIError(NetBoxClientError):
    """API error from NetBox."""

    def __init__(self, message: str, status_code: int | None = None, response_data: Any = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_data = response_data


class NetBoxClient:
    """
    NetBox API client using pynetbox.

    Supports both v1 and v2 API tokens.

    Args:
        url: NetBox instance URL (e.g., "https://netbox.example.com")
        token: API token (v1 or v2 format)
        verify_ssl: Whether to verify SSL certificates
        timeout: Request timeout in seconds
    """

    def __init__(
        self,
        url: str,
        token: str,
        *,
        verify_ssl: bool = True,
        timeout: float = 30.0,
    ) -> None:
        if not _PYNETBOX_INSTALLED:
            msg = "pynetbox is required for NetBox integration. Install with: pip install 'pyavd[netbox]'"
            raise ImportError(msg)

        self.base_url = url.rstrip("/")
        self.token = token
        self.verify_ssl = verify_ssl
        self.timeout = timeout
        self._api: Any = None

    @property
    def api(self) -> Any:
        """Get or create pynetbox API instance."""
        if self._api is None:
            self._api = _pynetbox.api(self.base_url, token=self.token)
            # Configure SSL verification and timeout
            if self._api.http_session is not None:
                self._api.http_session.verify = self.verify_ssl
                self._api.http_session.timeout = self.timeout
        return self._api

    def close(self) -> None:
        """Close the client (no-op for pynetbox, kept for compatibility)."""
        self._api = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _get_endpoint(self, endpoint_path: str) -> Any:
        """Get pynetbox endpoint from path string."""
        # Strip trailing ID if present (e.g., /api/dcim/devices/123/)
        base_path = re.sub(r"\d+/$", "", endpoint_path)
        if not base_path.endswith("/"):
            base_path += "/"

        if base_path not in ENDPOINT_MAP:
            msg = f"Unknown endpoint: {endpoint_path}"
            raise NetBoxAPIError(msg)

        app_name, endpoint_name = ENDPOINT_MAP[base_path]
        return getattr(getattr(self.api, app_name), endpoint_name)

    def _record_to_dict(self, record: Any) -> dict[str, Any]:
        """Convert pynetbox record to dictionary."""
        if record is None:
            return {}
        if isinstance(record, dict):
            return record
        return dict(record)

    def _handle_request_error(self, err: Exception) -> None:
        """Convert pynetbox RequestError to our custom exceptions."""
        error_msg = str(err)
        # Extract status code if available
        status_code = getattr(err, "status_code", None) if hasattr(err, "status_code") else None

        if "401" in error_msg or "authentication" in error_msg.lower():
            msg = "Invalid or expired API token"
            raise NetBoxAuthError(msg) from err
        if "403" in error_msg or "permission" in error_msg.lower():
            msg = "Insufficient permissions for this operation"
            raise NetBoxAuthError(msg) from err

        msg = f"NetBox API error: {error_msg}"
        raise NetBoxAPIError(msg, status_code=status_code) from err

    def get(self, endpoint: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """
        Make a GET request to NetBox API.

        Returns dict with 'results' key for compatibility with existing code.
        """
        try:
            ep = self._get_endpoint(endpoint)
            params = params or {}

            # Use filter with params
            results = list(ep.filter(**params))
            return {"results": [self._record_to_dict(r) for r in results], "count": len(results)}
        except _RequestError as e:
            self._handle_request_error(e)
            return {"results": [], "count": 0}  # Never reached, but keeps type checker happy

    def post(self, endpoint: str, data: dict[str, Any] | list[dict[str, Any]]) -> dict[str, Any]:
        """Make a POST request to NetBox API (create object)."""
        try:
            ep = self._get_endpoint(endpoint)
            result = ep.create(data)
            return self._record_to_dict(result)
        except _RequestError as e:
            self._handle_request_error(e)
            return {}  # Never reached

    def patch(self, endpoint: str, data: dict[str, Any]) -> dict[str, Any]:
        """Make a PATCH request to NetBox API (update object)."""
        try:
            # Extract ID from endpoint path (e.g., /api/dcim/devices/123/)
            match = re.search(r"/(\d+)/?$", endpoint)
            if not match:
                msg = f"Cannot extract ID from endpoint: {endpoint}"
                raise NetBoxAPIError(msg)

            obj_id = int(match.group(1))
            ep = self._get_endpoint(endpoint)

            # Get the object and update it
            obj = ep.get(obj_id)
            if obj is None:
                msg = f"Object not found: {endpoint}"
                raise NetBoxAPIError(msg)

            # Update attributes
            for key, value in data.items():
                setattr(obj, key, value)
            obj.save()

            return self._record_to_dict(obj)
        except _RequestError as e:
            self._handle_request_error(e)
            return {}  # Never reached

    def delete(self, endpoint: str) -> None:
        """Make a DELETE request to NetBox API."""
        try:
            match = re.search(r"/(\d+)/?$", endpoint)
            if not match:
                msg = f"Cannot extract ID from endpoint: {endpoint}"
                raise NetBoxAPIError(msg)

            obj_id = int(match.group(1))
            ep = self._get_endpoint(endpoint)

            obj = ep.get(obj_id)
            if obj:
                obj.delete()
        except _RequestError as e:
            self._handle_request_error(e)

    def get_all(self, endpoint: str, params: dict[str, Any] | None = None) -> Iterator[dict[str, Any]]:
        """
        Get all objects from an endpoint.

        Yields each object as a dictionary. Pynetbox handles pagination automatically.
        """
        try:
            ep = self._get_endpoint(endpoint)
            params = params or {}

            # pynetbox handles pagination automatically
            for record in ep.filter(**params) if params else ep.all():
                yield self._record_to_dict(record)
        except _RequestError as e:
            self._handle_request_error(e)
