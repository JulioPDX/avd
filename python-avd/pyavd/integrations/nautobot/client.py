# Copyright (c) 2023-2026 Arista Networks, Inc.
# Use of this source code is governed by the Apache License 2.0
# that can be found in the LICENSE file.
"""
Nautobot API Client.

Simple HTTP client using httpx for interacting with Nautobot REST API.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

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


class NautobotClientError(Exception):
    """Base exception for Nautobot client errors."""


class NautobotAuthError(NautobotClientError):
    """Authentication error with Nautobot."""


class NautobotAPIError(NautobotClientError):
    """API error from Nautobot."""

    def __init__(self, message: str, status_code: int | None = None, response_data: Any = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_data = response_data


class AsyncNautobotClient:
    """
    Async Nautobot API client using httpx.AsyncClient.

    High-performance async HTTP client with connection pooling for concurrent requests.

    Args:
        url: Nautobot instance URL (e.g., "http://nautobot.example.com")
        token: API token
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
            msg = "httpx is required for Nautobot integration. Install with: pip install 'pyavd[nautobot]'"
            raise ImportError(msg)

        self.base_url = url.rstrip("/")
        self.timeout = timeout
        self.max_concurrent = max_concurrent

        self._client = _httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                "Authorization": f"Token {token}",
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
            raise NautobotAuthError(msg)
        if response.status_code == 403:
            msg = "Insufficient permissions for this operation"
            raise NautobotAuthError(msg)
        if response.status_code >= 400:
            try:
                error_data = response.json()
            except Exception:
                error_data = response.text
            msg = f"The request failed with code {response.status_code} {response.reason_phrase}: {error_data}"
            raise NautobotAPIError(msg, status_code=response.status_code, response_data=error_data)

        if response.status_code == 204:
            return {}
        return response.json()

    async def get(self, endpoint: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Make an async GET request to Nautobot API."""
        response = await self._client.get(endpoint, params=params)
        return self._handle_response(response)

    async def post(self, endpoint: str, data: dict[str, Any] | list[dict[str, Any]]) -> dict[str, Any]:
        """Make an async POST request to Nautobot API (create object)."""
        response = await self._client.post(endpoint, json=data)
        return self._handle_response(response)

    async def patch(self, endpoint: str, data: dict[str, Any]) -> dict[str, Any]:
        """Make an async PATCH request to Nautobot API (update object)."""
        response = await self._client.patch(endpoint, json=data)
        return self._handle_response(response)

    async def delete(self, endpoint: str) -> None:
        """Make an async DELETE request to Nautobot API."""
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

    async def bulk_create(self, endpoint: str, data: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Bulk create objects using Nautobot's bulk API.

        Args:
            endpoint: API endpoint (e.g., "/api/dcim/interfaces/")
            data: List of objects to create

        Returns:
            List of created objects
        """
        if not data:
            return []

        response = await self._client.post(endpoint, json=data)
        result = self._handle_response(response)
        # Nautobot bulk endpoints return a list
        return result if isinstance(result, list) else [result]

    async def bulk_update(self, endpoint: str, data: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Bulk update objects using Nautobot's bulk API.

        Args:
            endpoint: API endpoint (e.g., "/api/dcim/interfaces/")
            data: List of objects to update (must include 'id' field)

        Returns:
            List of updated objects
        """
        if not data:
            return []

        response = await self._client.patch(endpoint, json=data)
        result = self._handle_response(response)
        return result if isinstance(result, list) else [result]

    async def bulk_delete(self, endpoint: str, ids: list[str]) -> int:
        """
        Bulk delete objects using Nautobot's bulk API.

        Args:
            endpoint: API endpoint (e.g., "/api/dcim/interfaces/")
            ids: List of object IDs to delete

        Returns:
            Number of objects deleted
        """
        if not ids:
            return 0

        # Nautobot bulk delete uses POST with a list of IDs
        data = [{"id": obj_id} for obj_id in ids]
        response = await self._client.delete(endpoint, json=data)
        self._handle_response(response)
        return len(ids)
