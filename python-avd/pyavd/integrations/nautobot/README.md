<!--
  ~ Copyright (c) 2026 Arista Networks, Inc.
  ~ Use of this source code is governed by the Apache License 2.0
  ~ that can be found in the LICENSE file.
  -->

# AVD to Nautobot Integration

This module provides high-performance async synchronization from AVD (Arista Validated Designs)
structured configuration data to Nautobot DCIM/IPAM models.

## Features

- **Async/Concurrent** - Uses httpx with asyncio for high-performance API calls
- **Bulk Operations** - Leverages Nautobot's bulk API endpoints for faster sync
- **Parent Prefix Creation** - Automatically creates parent prefixes (/24, /64) for IP addresses
- **Tag-Based Management** - All synced objects are tagged for tracking and reconciliation
- **Reconcile Mode** - Optionally delete orphaned objects not present in AVD configs
- **Dry Run** - Preview changes without modifying Nautobot
- **Purge Mode** - Clean up all AVD-managed objects

## Supported Object Types

| Object Type | AVD Source |
| ----------- | ---------- |
| Devices | hostname, platform, etc. |
| Interfaces | ethernet_interfaces, loopback_interfaces, vlan_interfaces, port_channel_interfaces |
| IP Addresses | ip_address from interfaces |
| VLANs | vlans |
| VRFs | vrfs |
| Prefixes | Derived from IP addresses (both /32 host routes and /24 parent prefixes) |
| Cables | Physical connections between interfaces |

## Quick Start

```python
import asyncio
from pyavd.integrations.nautobot import AsyncNautobotClient, AsyncAVDNautobotSync

async def sync_to_nautobot():
    async with AsyncNautobotClient("http://nautobot.example.com", "api_token") as client:
        sync = AsyncAVDNautobotSync(client, location_name="DC1", create_prerequisites=True)
        result = await sync.sync_all(avd_configs)
        print(f"Created: {result.created}, Updated: {result.updated}, Errors: {len(result.errors)}")

asyncio.run(sync_to_nautobot())
```

## Architecture

```text
pyavd/integrations/nautobot/
├── __init__.py          # Public API exports
├── async_sync.py        # Main async sync class (AsyncAVDNautobotSync)
├── async_device.py      # Async device sync mixin methods
├── async_helpers.py     # Async helper methods (caching, tags, etc.)
├── async_interface.py   # Async interface/IP sync mixin methods
├── client.py            # Async HTTP client for Nautobot REST API (AsyncNautobotClient)
├── models.py            # Data model mappings
└── transforms.py        # Data transformation functions
```

## API Reference

### AsyncNautobotClient

High-performance async HTTP client using httpx with connection pooling:

```python
async with AsyncNautobotClient(url, token) as client:
    # GET request
    devices = await client.get("/api/dcim/devices/")

    # POST request
    new_device = await client.post("/api/dcim/devices/", device_data)

    # Bulk create
    created = await client.bulk_create("/api/dcim/interfaces/", [intf1, intf2, intf3])
```

### AsyncAVDNautobotSync

Main synchronization class with the following parameters:

| Parameter | Type | Default | Description |
| --------- | ---- | ------- | ----------- |
| client | AsyncNautobotClient | required | Nautobot API client |
| location_name | str | None | Default location for new objects |
| location_mapping | dict | {} | Map hostname prefixes to locations |
| create_prerequisites | bool | True | Auto-create locations, device types, etc. |
| managed_tag | str | "avd-managed" | Tag to identify AVD-managed objects |
| reconcile | bool | False | Delete orphaned objects after sync |
| max_concurrent | int | 10 | Max concurrent device processing |
| dry_run | bool | False | Preview changes without modifying |

## Purge Mode

The `purge_all()` method deletes **ALL** objects tagged with the managed tag from Nautobot.

### Basic Purge

```python
async def purge_nautobot():
    async with AsyncNautobotClient("http://nautobot.example.com", "token") as client:
        sync = AsyncAVDNautobotSync(client, managed_tag="avd-managed")

        # Preview what would be deleted
        result = await sync.purge_all(dry_run=True)
        print(f"Would delete: {result.skipped} objects")

        # Actually delete
        result = await sync.purge_all()
        print(f"Deleted: {result.deleted} objects")
```

### Purge with Prerequisites

To also delete prerequisite objects (locations, device types, platforms, manufacturers, roles):

```python
async def purge_everything():
    async with AsyncNautobotClient("http://nautobot.example.com", "token") as client:
        sync = AsyncAVDNautobotSync(client, managed_tag="avd-managed")

        # Delete everything including prerequisites
        result = await sync.purge_all(purge_prerequisites=True)
        print(f"Deleted: {result.deleted} objects")
```

## Parent Prefix Creation

Nautobot requires a parent prefix to exist before creating IP addresses within a namespace.
This integration automatically creates parent prefixes (/24 for IPv4, /64 for IPv6) for all
IP addresses found in the AVD configuration.

For example, if an interface has IP `10.255.0.1/32`:

1. A `/24` parent prefix `10.255.0.0/24` is created first
2. Then the `/32` host prefix `10.255.0.1/32` is created
3. Finally, the IP address `10.255.0.1/32` is created and assigned to the interface

This ensures all IP addresses can be successfully created in Nautobot.

## Reconcile Mode

Enable reconcile mode to delete objects that exist in Nautobot but not in the AVD configs:

```python
sync = AsyncAVDNautobotSync(client, location_name="DC1", reconcile=True)
result = await sync.sync_all(configs)
print(f"Deleted orphaned objects: {result.deleted}")
```

## Nautobot-Specific Considerations

- **Locations** - Nautobot uses Locations instead of Sites (uses `/api/dcim/locations/`)
- **Roles** - Device roles are in `/api/extras/roles/` with required `content_types`
- **Statuses** - Explicit status objects in `/api/extras/statuses/` with UUIDs
- **Namespaces** - IP addresses and prefixes are scoped to namespaces (default: "Global")
- **IP-to-Interface** - Assignments use separate `/api/ipam/ip-address-to-interface/` endpoint

### Tag Support Limitations

In Nautobot, **manufacturers**, **platforms**, and **roles** do not support tags in the API.
These object types do not have a `tags` field in their API responses, and attempting to create
tags with these content types results in API errors.

**Objects that support tags (can be tracked and purged):**

- Locations
- Devices
- Device Types
- Interfaces
- Cables
- VLANs
- VRFs
- Prefixes
- IP Addresses

**Objects that do NOT support tags (must be manually deleted):**

- Manufacturers
- Platforms
- Roles

When using `purge_all(purge_prerequisites=True)`, only locations and device types will be
deleted. Manufacturers, platforms, and roles must be manually removed from Nautobot if cleanup
is required.
