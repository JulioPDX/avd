# Copyright (c) 2023-2026 Arista Networks, Inc.
# Use of this source code is governed by the Apache License 2.0
# that can be found in the LICENSE file.
"""
AVD to Nautobot Integration Module.

This module provides synchronization between AVD structured configuration data
and Nautobot DCIM/IPAM models.

Example usage:
    import asyncio
    from pyavd.integrations.nautobot import AsyncNautobotClient, AsyncAVDNautobotSync

    async def sync_to_nautobot():
        async with AsyncNautobotClient("http://nautobot.example.com", "api_token") as client:
            sync = AsyncAVDNautobotSync(client, location_name="DC1")
            result = await sync.sync_all(avd_structured_configs)
            print(f"Created: {result.created}, Updated: {result.updated}")

    asyncio.run(sync_to_nautobot())
"""

from .async_sync import AsyncAVDNautobotSync, SyncResult
from .client import AsyncNautobotClient, NautobotAPIError, NautobotAuthError, NautobotClientError
from .models import AVDNautobotMapping, FieldMapping

__all__ = [
    "AVDNautobotMapping",
    "AsyncAVDNautobotSync",
    "AsyncNautobotClient",
    "FieldMapping",
    "NautobotAPIError",
    "NautobotAuthError",
    "NautobotClientError",
    "SyncResult",
]
