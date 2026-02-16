<!--
  ~ Copyright (c) 2026 Arista Networks, Inc.
  ~ Use of this source code is governed by the Apache License 2.0
  ~ that can be found in the LICENSE file.
  -->

# AVD-NetBox Integration

Synchronization from AVD (Arista Validated Designs) structured configuration data to NetBox DCIM/IPAM models.

## Overview

This integration enables you to document your Arista network fabric in NetBox by syncing devices, interfaces, VLANs, VRFs, IP addresses, and cable connections from AVD structured configs.

## Features

### AVD to NetBox Sync

| Data Type | Synced Fields |
| --------- | ------------- |
| Devices | hostname, platform, serial, device role, site, status |
| Ethernet Interfaces | name, description, enabled, MTU, mode, speed, type, LAG membership |
| Loopback Interfaces | Loopback0, Loopback1, etc. with IP addresses |
| Management Interfaces | Management1 with IP, set as device primary_ip4 |
| VLAN Interfaces (SVIs) | Vlan interfaces with IP addresses and VRF assignment |
| Port-Channel Interfaces | LAG interfaces with member interface associations |
| VLANs | VLAN ID, name, status |
| VRFs | name, description |
| Interface VLAN Associations | tagged_vlans (trunk), untagged_vlan (access/native) |
| IP Prefixes | Subnets from loopback pools, P2P links, SVIs, management |
| ASNs | BGP AS numbers from router_bgp.as and neighbors |
| Cables | Physical connections between interfaces |

## Architecture

```text
pyavd/integrations/netbox/
├── __init__.py      # Public API exports
├── client.py        # HTTP client for NetBox REST API
├── models.py        # Data model mappings (FieldMapping, AVDNetBoxMapping)
├── sync.py          # Main sync logic (AVDNetBoxSync class)
└── transforms.py    # Data transformation functions
```

### Key Components

**NetBoxClient** - High-performance HTTP client using httpx with support for v1 and v2 API tokens:

```python
from pyavd.integrations.netbox import NetBoxClient

client = NetBoxClient("https://netbox.example.com", "nbt_xxx.yyy")
# Direct HTTP methods
devices = client.get("/api/dcim/devices/", params={"site": "dc1"})
```

**AVDNetBoxSync** - Main synchronization class:

```python
sync = AVDNetBoxSync(
    client,
    site_name="DC1",           # NetBox site to sync to
    dry_run=False,             # Set True to preview changes
    create_prerequisites=True  # Auto-create site, device types, etc.
)
```

**FieldMapping** - Maps AVD fields to NetBox fields with optional transformation:

```python
FieldMapping(
    avd_path="shutdown",      # AVD field path (dot notation)
    netbox_field="enabled",   # NetBox API field
    transform="invert_bool"   # Optional transform function
)
```

## Installation

The integration requires `httpx` for NetBox API communication:

```bash
pip install 'pyavd[netbox]'
# Or install httpx directly
pip install httpx
```

## Quick Start

```python
from pyavd.integrations.netbox import NetBoxClient, AVDNetBoxSync

# Load your AVD structured configs (dict of hostname -> config)
configs = {"dc1-spine1": {...}, "dc1-leaf1a": {...}}
node_types = {"dc1-spine1": "spine", "dc1-leaf1a": "l3leaf"}

client = NetBoxClient("https://netbox.example.com", "nbt_xxx.yyy")
sync = AVDNetBoxSync(client, site_name="DC1", create_prerequisites=True)
result = sync.sync_all(configs, node_types)

print(f"Created: {result.created}")
print(f"Updated: {result.updated}")
print(f"Errors: {len(result.errors)}")
```

## API Reference

### NetBoxClient

High-performance HTTP client using httpx with connection pooling:

```python
class NetBoxClient:
    def __init__(self, url: str, token: str, *, verify_ssl: bool = True, timeout: float = 30.0)

    # HTTP methods
    def get(self, endpoint: str, params: dict = None) -> dict
    def post(self, endpoint: str, data: dict) -> dict
    def patch(self, endpoint: str, data: dict) -> dict
    def delete(self, endpoint: str) -> None
    def get_all(self, endpoint: str, params: dict = None) -> Iterator[dict]  # Auto-pagination
```

### AVDNetBoxSync

```python
class AVDNetBoxSync:
    def __init__(
        self,
        client: NetBoxClient,
        site_name: str = None,
        site_mapping: dict[str, str] = None,  # Map hostname prefix to site name
        dry_run: bool = False,
        create_prerequisites: bool = False,
        managed_tag: str = "avd-managed",     # Tag for reconciliation
        reconcile: bool = False,              # Delete orphaned objects
    )

    # Main entry point - syncs everything
    def sync_all(self, configs: dict, node_types: dict = None) -> SyncResult

    # Individual sync methods
    def sync_device(self, config: dict, node_type: str = None) -> SyncResult
    def sync_interfaces(self, config: dict) -> SyncResult
    def sync_vlans(self, config: dict) -> SyncResult
    def sync_vrfs(self, config: dict) -> SyncResult
    def sync_cables(self, configs: dict) -> SyncResult
    def sync_primary_ip(self, config: dict) -> SyncResult

    # Additional sync methods
    def sync_prefix(self, prefix: str, vrf_name: str = None, description: str = "") -> SyncResult
    def sync_prefixes_from_config(self, config: dict) -> SyncResult
    def sync_asn(self, asn: int | str) -> SyncResult
    def sync_asns_from_config(self, config: dict) -> SyncResult
    def sync_port_channels(self, config: dict) -> SyncResult
    def sync_interface_vlan_associations(self, config: dict) -> SyncResult

    # Reconciliation
    def reconcile_objects(self, dry_run: bool = None) -> SyncResult
```

### SyncResult

```python
@dataclass
class SyncResult:
    created: int = 0      # Items created in NetBox
    updated: int = 0      # Items updated in NetBox
    skipped: int = 0      # Items unchanged
    deleted: int = 0      # Items deleted from NetBox (reconciliation)
    errors: list[str]     # Error messages
```

## Transform Functions

Available transforms for field mappings (in `transforms.py`):

| Transform | Description | Example |
| --------- | ----------- | ------- |
| `slugify` | Convert to NetBox slug format | `"DC-1 Spine"` → `"dc-1-spine"` |
| `invert_bool` | Invert boolean (shutdown → enabled) | `True` → `False` |
| `map_interface_mode` | AVD mode to NetBox mode | `"trunk"` → `"tagged"` |
| `map_interface_type` | Interface name to type | `"Loopback0"` → `"virtual"` |
| `map_vlan_status` | VLAN status mapping | `"suspend"` → `"deprecated"` |
| `parse_speed` | Parse speed string to int | `"10g"` → `10000000` |

## Reconciliation (Garbage Collection)

The integration supports automatic cleanup of orphaned objects in NetBox. When enabled,
objects that are tagged with the managed tag (`avd-managed` by default) but no longer
exist in AVD configs will be deleted.

```python
from pyavd.integrations.netbox import NetBoxClient, AVDNetBoxSync

client = NetBoxClient("https://netbox.example.com", "nbt_xxx.yyy")
sync = AVDNetBoxSync(
    client,
    site_name="DC1",
    reconcile=True,           # Enable reconciliation
    managed_tag="avd-managed" # Tag for managed objects (default)
)

# All synced objects get tagged automatically
result = sync.sync_all(configs, node_types)

# Objects with the tag that weren't touched are deleted at the end
# Deletion order: cables → IPs → interfaces → prefixes → VLANs → VRFs → ASNs → devices
```

## Testing

Run the unit test suite:

```bash
cd python-avd
pytest tests/pyavd/integrations/netbox/ -v
```

**Test coverage includes:**

- HTTP client with httpx
- API operations (GET, POST, PATCH, DELETE)
- Pagination handling
- All transform functions
- Sync operations for all data types (120+ tests total)

## Development

### Adding New Field Mappings

1. Add mapping to `models.py`:

```python
FieldMapping("avd_field.path", "netbox_field", transform="optional_transform")
```

1. If needed, add transform function to `transforms.py`:

```python
def my_transform(value: Any) -> Any:
    return transformed_value

TRANSFORMS["my_transform"] = my_transform
```

## License

Apache License 2.0 - See LICENSE file in repository root.
