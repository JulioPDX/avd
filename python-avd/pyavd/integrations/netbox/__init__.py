# Copyright (c) 2023-2026 Arista Networks, Inc.
# Use of this source code is governed by the Apache License 2.0
# that can be found in the LICENSE file.
"""
AVD to NetBox Integration Module.

This module provides synchronization between AVD structured configuration data
and NetBox DCIM/IPAM models.

Example usage:
    from pyavd.integrations.netbox import NetBoxClient, AVDNetBoxSync

    # Create client with v2 token
    with NetBoxClient("https://netbox.example.com", "nbt_key.token") as client:
        sync = AVDNetBoxSync(client, site_name="DC1")

        # Sync all devices
        result = sync.sync_all(avd_structured_configs)
        print(f"Created: {result.created}, Updated: {result.updated}")
"""

from .client import NetBoxAPIError, NetBoxAuthError, NetBoxClient, NetBoxClientError
from .models import AVDNetBoxMapping, FieldMapping, SyncDirection
from .sync import AVDNetBoxSync, SyncResult

__all__ = [
    "AVDNetBoxMapping",
    "AVDNetBoxSync",
    "FieldMapping",
    "NetBoxAPIError",
    "NetBoxAuthError",
    "NetBoxClient",
    "NetBoxClientError",
    "SyncDirection",
    "SyncResult",
]
