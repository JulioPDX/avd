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
| Ethernet Interfaces | name, description, enabled, MTU, mode, speed, type |
| Loopback Interfaces | Loopback0, Loopback1, etc. with IP addresses |
| Management Interfaces | Management1 with IP, set as device primary_ip4 |
| VLAN Interfaces (SVIs) | Vlan interfaces with IP addresses and VRF assignment |
| VLANs | VLAN ID, name, status |
| VRFs | name, description |
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

**NetBoxClient** - HTTP client with support for v1 and v2 API tokens:

```python
# v2 tokens (nbt_*) use Bearer auth
# v1 tokens use Token auth
with NetBoxClient("https://netbox.example.com", "nbt_xxx.yyy") as client:
    ...
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

The integration requires `httpx` for HTTP communication:

```bash
pip install httpx
```

## Quick Start

```python
from pyavd.integrations.netbox import NetBoxClient, AVDNetBoxSync

# Load your AVD structured configs (dict of hostname -> config)
configs = {"dc1-spine1": {...}, "dc1-leaf1a": {...}}
node_types = {"dc1-spine1": "spine", "dc1-leaf1a": "l3leaf"}

with NetBoxClient("https://netbox.example.com", "nbt_xxx.yyy") as client:
    sync = AVDNetBoxSync(client, site_name="DC1", create_prerequisites=True)
    result = sync.sync_all(configs, node_types)

    print(f"Created: {result.created}")
    print(f"Updated: {result.updated}")
    print(f"Errors: {len(result.errors)}")
```

## API Reference

### NetBoxClient

```python
class NetBoxClient:
    def __init__(self, url: str, token: str, *, verify_ssl: bool = True, timeout: float = 30.0)
    def get(self, endpoint: str, **params) -> dict
    def post(self, endpoint: str, data: dict) -> dict
    def patch(self, endpoint: str, data: dict) -> dict
    def delete(self, endpoint: str) -> None
    def get_all(self, endpoint: str, **params) -> Iterator[dict]  # Paginated
```

### AVDNetBoxSync

```python
class AVDNetBoxSync:
    def __init__(self, client: NetBoxClient, site_name: str = None,
                 dry_run: bool = False, create_prerequisites: bool = False)

    def sync_all(self, configs: dict, node_types: dict = None) -> SyncResult
    def sync_device(self, config: dict, node_type: str = None) -> SyncResult
    def sync_interface(self, device_id: int, interface: dict) -> SyncResult
    def sync_vlan(self, vlan: dict) -> SyncResult
    def sync_vrf(self, vrf: dict) -> SyncResult
    def sync_cable(self, a_device: str, a_interface: str,
                   b_device: str, b_interface: str) -> SyncResult
```

### SyncResult

```python
@dataclass
class SyncResult:
    created: int = 0      # Items created in NetBox
    updated: int = 0      # Items updated in NetBox
    skipped: int = 0      # Items unchanged
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

## Testing

Run the unit test suite:

```bash
cd python-avd
pytest tests/pyavd/integrations/netbox/ -v
```

**Test coverage includes:**

- Client authentication (v1 and v2 tokens)
- API operations (GET, POST, PATCH, DELETE)
- Pagination handling
- All transform functions
- Sync operations for all data types

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
