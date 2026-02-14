# Copyright (c) 2023-2026 Arista Networks, Inc.
# Use of this source code is governed by the Apache License 2.0
# that can be found in the LICENSE file.
"""
NetBox API Client.

HTTP client for interacting with NetBox REST API.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator

    from typing_extensions import Self

try:
    import httpx
except ImportError as import_err:
    msg = "httpx is required for NetBox integration. Install with: pip install httpx"
    raise ImportError(msg) from import_err

LOGGER = logging.getLogger(__name__)


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
    HTTP client for NetBox REST API.

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
        self.base_url = url.rstrip("/")
        self.token = token
        self.verify_ssl = verify_ssl
        self.timeout = timeout
        self._client: httpx.Client | None = None

    @property
    def _auth_header(self) -> dict[str, str]:
        """Build authorization header based on token format."""
        # v2 tokens start with "nbt_"
        if self.token.startswith("nbt_"):
            return {"Authorization": f"Bearer {self.token}"}
        # v1 tokens use "Token" prefix
        return {"Authorization": f"Token {self.token}"}

    @property
    def client(self) -> httpx.Client:
        """Get or create HTTP client."""
        if self._client is None:
            self._client = httpx.Client(
                base_url=self.base_url,
                headers={
                    **self._auth_header,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                verify=self.verify_ssl,
                timeout=self.timeout,
            )
        return self._client

    def close(self) -> None:
        """Close the HTTP client."""
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _handle_response(self, response: httpx.Response) -> Any:
        """Handle API response and raise appropriate errors."""
        if response.status_code == 401:
            msg = "Invalid or expired API token"
            raise NetBoxAuthError(msg)
        if response.status_code == 403:
            msg = "Insufficient permissions for this operation"
            raise NetBoxAuthError(msg)

        try:
            data = response.json() if response.content else None
        except Exception:
            data = response.text

        if response.status_code >= 400:
            msg = f"NetBox API error: {response.status_code}"
            raise NetBoxAPIError(msg, status_code=response.status_code, response_data=data)

        return data

    def get(self, endpoint: str, params: dict[str, Any] | None = None) -> Any:
        """Make a GET request to NetBox API."""
        response = self.client.get(endpoint, params=params)
        return self._handle_response(response)

    def post(self, endpoint: str, data: dict[str, Any] | list[dict[str, Any]]) -> Any:
        """Make a POST request to NetBox API."""
        response = self.client.post(endpoint, json=data)
        return self._handle_response(response)

    def patch(self, endpoint: str, data: dict[str, Any]) -> Any:
        """Make a PATCH request to NetBox API."""
        response = self.client.patch(endpoint, json=data)
        return self._handle_response(response)

    def delete(self, endpoint: str) -> None:
        """Make a DELETE request to NetBox API."""
        response = self.client.delete(endpoint)
        if response.status_code not in (200, 204):
            self._handle_response(response)

    def get_all(self, endpoint: str, params: dict[str, Any] | None = None) -> Iterator[dict[str, Any]]:
        """
        Get all objects from a paginated endpoint.

        Yields each object from all pages.
        """
        params = params or {}
        params.setdefault("limit", 100)

        while True:
            data = self.get(endpoint, params)
            if not isinstance(data, dict) or "results" not in data:
                break

            yield from data["results"]

            if not data.get("next"):
                break

            # Extract offset from next URL
            params["offset"] = params.get("offset", 0) + params["limit"]
