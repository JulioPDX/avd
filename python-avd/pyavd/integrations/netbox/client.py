# Copyright (c) 2023-2026 Arista Networks, Inc.
# Use of this source code is governed by the Apache License 2.0
# that can be found in the LICENSE file.
"""
NetBox API Client.

Simple HTTP client using httpx for interacting with NetBox REST API.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from typing_extensions import Self

# httpx is an optional dependency
_HTTPX_INSTALLED = False
_httpx: Any = None

try:
    import httpx as _httpx_module

    _httpx = _httpx_module
    _HTTPX_INSTALLED = True
except ImportError:
    pass

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
    NetBox API client using httpx.

    High-performance HTTP client with connection pooling.
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
        if not _HTTPX_INSTALLED:
            msg = "httpx is required for NetBox integration. Install with: pip install 'pyavd[netbox]'"
            raise ImportError(msg)

        self.base_url = url.rstrip("/")
        self.timeout = timeout

        # Determine auth header format (v2 tokens start with "nbt_")
        auth_header = f"Token {token}"
        if token.startswith("nbt_"):
            auth_header = f"Bearer {token}"

        self._client = _httpx.Client(
            base_url=self.base_url,
            headers={
                "Authorization": auth_header,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            verify=verify_ssl,
            timeout=timeout,
        )

    def close(self) -> None:
        """Close the HTTP client and release connections."""
        self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _handle_response(self, response: Any) -> dict[str, Any]:
        """Handle HTTP response, raising appropriate exceptions for errors."""
        if response.status_code == 401:
            msg = "Invalid or expired API token"
            raise NetBoxAuthError(msg)
        if response.status_code == 403:
            msg = "Insufficient permissions for this operation"
            raise NetBoxAuthError(msg)
        if response.status_code >= 400:
            try:
                error_data = response.json()
            except Exception:
                error_data = response.text
            msg = f"The request failed with code {response.status_code} {response.reason_phrase}: {error_data}"
            raise NetBoxAPIError(msg, status_code=response.status_code, response_data=error_data)

        if response.status_code == 204:
            return {}
        return response.json()

    def get(self, endpoint: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """
        Make a GET request to NetBox API.

        Returns dict with 'results' key for list endpoints.
        """
        response = self._client.get(endpoint, params=params)
        return self._handle_response(response)

    def post(self, endpoint: str, data: dict[str, Any] | list[dict[str, Any]]) -> dict[str, Any]:
        """Make a POST request to NetBox API (create object)."""
        response = self._client.post(endpoint, json=data)
        return self._handle_response(response)

    def patch(self, endpoint: str, data: dict[str, Any]) -> dict[str, Any]:
        """Make a PATCH request to NetBox API (update object)."""
        response = self._client.patch(endpoint, json=data)
        return self._handle_response(response)

    def delete(self, endpoint: str) -> None:
        """Make a DELETE request to NetBox API."""
        response = self._client.delete(endpoint)
        self._handle_response(response)

    def get_all(self, endpoint: str, params: dict[str, Any] | None = None) -> Iterator[dict[str, Any]]:
        """
        Get all objects from an endpoint with automatic pagination.

        Yields each object as a dictionary.
        """
        params = params.copy() if params else {}
        params.setdefault("limit", 1000)  # Use large page size for efficiency

        while True:
            response = self.get(endpoint, params)
            results = response.get("results", [])

            yield from results

            # Check for next page
            next_url = response.get("next")
            if not next_url:
                break

            # Extract offset from next URL for subsequent request
            # NetBox returns full URL, we need to extract params
            if "offset=" in next_url:
                match = re.search(r"offset=(\d+)", next_url)
                if match:
                    params["offset"] = int(match.group(1))
                else:
                    break
            else:
                break


class AsyncNetBoxClient:
    """
    Async NetBox API client using httpx.AsyncClient.

    High-performance async HTTP client with connection pooling for concurrent requests.
    Supports both v1 and v2 API tokens.

    Args:
        url: NetBox instance URL (e.g., "https://netbox.example.com")
        token: API token (v1 or v2 format)
        verify_ssl: Whether to verify SSL certificates
        timeout: Request timeout in seconds
        max_concurrent: Maximum concurrent requests (semaphore limit)
    """

    def __init__(
        self,
        url: str,
        token: str,
        *,
        verify_ssl: bool = True,
        timeout: float = 30.0,
        max_concurrent: int = 10,
    ) -> None:
        if not _HTTPX_INSTALLED:
            msg = "httpx is required for NetBox integration. Install with: pip install 'pyavd[netbox]'"
            raise ImportError(msg)

        self.base_url = url.rstrip("/")
        self.timeout = timeout
        self.max_concurrent = max_concurrent

        # Determine auth header format (v2 tokens start with "nbt_")
        auth_header = f"Token {token}"
        if token.startswith("nbt_"):
            auth_header = f"Bearer {token}"

        self._client = _httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                "Authorization": auth_header,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            verify=verify_ssl,
            timeout=timeout,
        )

    async def close(self) -> None:
        """Close the HTTP client and release connections."""
        await self._client.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.close()

    def _handle_response(self, response: Any) -> dict[str, Any]:
        """Handle HTTP response, raising appropriate exceptions for errors."""
        if response.status_code == 401:
            msg = "Invalid or expired API token"
            raise NetBoxAuthError(msg)
        if response.status_code == 403:
            msg = "Insufficient permissions for this operation"
            raise NetBoxAuthError(msg)
        if response.status_code >= 400:
            try:
                error_data = response.json()
            except Exception:
                error_data = response.text
            msg = f"The request failed with code {response.status_code} {response.reason_phrase}: {error_data}"
            raise NetBoxAPIError(msg, status_code=response.status_code, response_data=error_data)

        if response.status_code == 204:
            return {}
        return response.json()

    async def get(self, endpoint: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Make an async GET request to NetBox API."""
        response = await self._client.get(endpoint, params=params)
        return self._handle_response(response)

    async def post(self, endpoint: str, data: dict[str, Any] | list[dict[str, Any]]) -> dict[str, Any]:
        """Make an async POST request to NetBox API (create object)."""
        response = await self._client.post(endpoint, json=data)
        return self._handle_response(response)

    async def patch(self, endpoint: str, data: dict[str, Any]) -> dict[str, Any]:
        """Make an async PATCH request to NetBox API (update object)."""
        response = await self._client.patch(endpoint, json=data)
        return self._handle_response(response)

    async def delete(self, endpoint: str) -> None:
        """Make an async DELETE request to NetBox API."""
        response = await self._client.delete(endpoint)
        self._handle_response(response)

    async def get_all(self, endpoint: str, params: dict[str, Any] | None = None) -> AsyncIterator[dict[str, Any]]:
        """
        Get all objects from an endpoint with automatic pagination.

        Async yields each object as a dictionary.
        """
        params = params.copy() if params else {}
        params.setdefault("limit", 1000)

        while True:
            response = await self.get(endpoint, params)
            results = response.get("results", [])

            for item in results:
                yield item

            next_url = response.get("next")
            if not next_url:
                break

            if "offset=" in next_url:
                match = re.search(r"offset=(\d+)", next_url)
                if match:
                    params["offset"] = int(match.group(1))
                else:
                    break
            else:
                break

    async def get_all_list(self, endpoint: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """
        Get all objects from an endpoint as a list.

        Convenience method that collects all paginated results into a list.
        """
        return [item async for item in self.get_all(endpoint, params)]
