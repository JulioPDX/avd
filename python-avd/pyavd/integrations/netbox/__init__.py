# Copyright (c) 2023-2026 Arista Networks, Inc.
# Use of this source code is governed by the Apache License 2.0
# that can be found in the LICENSE file.
"""
AVD to NetBox Integration Module.

This module provides synchronization between AVD structured configuration data
and NetBox DCIM/IPAM models.

Example usage:
    import asyncio
    from pyavd.integrations.netbox import AsyncNetBoxClient, AsyncAVDNetBoxSync

    async def sync_to_netbox():
        async with AsyncNetBoxClient("https://netbox.example.com", "nbt_key.token") as client:
            sync = AsyncAVDNetBoxSync(client, site_name="DC1")
            result = await sync.sync_all(avd_structured_configs)
            print(f"Created: {result.created}, Updated: {result.updated}")

    asyncio.run(sync_to_netbox())
"""

from .async_sync import AsyncAVDNetBoxSync, SyncResult
from .client import AsyncNetBoxClient, NetBoxAPIError, NetBoxAuthError, NetBoxClientError
from .models import AVDNetBoxMapping, FieldMapping

__all__ = [
    "AVDNetBoxMapping",
    "AsyncAVDNetBoxSync",
    "AsyncNetBoxClient",
    "FieldMapping",
    "NetBoxAPIError",
    "NetBoxAuthError",
    "NetBoxClientError",
    "SyncResult",
]
