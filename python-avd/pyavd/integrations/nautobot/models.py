# Copyright (c) 2023-2026 Arista Networks, Inc.
# Use of this source code is governed by the Apache License 2.0
# that can be found in the LICENSE file.
"""
AVD to Nautobot Model Mappings.

Defines the mapping between AVD structured config fields and Nautobot API models.
Nautobot is a fork of NetBox with some differences in API structure:
- Uses 'locations' instead of 'sites'
- Roles are in /api/extras/roles/
- Statuses are explicit objects in /api/extras/statuses/
- Uses UUIDs for all object IDs
- IP addresses are assigned via /api/ipam/ip-address-to-interface/
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FieldMapping:
    """Mapping between an AVD field and a Nautobot field."""

    avd_path: str  # Dot-notation path in AVD structured config
    nautobot_field: str  # Nautobot API field name
    transform: str | None = None  # Optional transformation function name


@dataclass
class AVDNautobotMapping:
    """
    Complete mapping configuration between AVD and Nautobot models.

    This class defines which AVD structured config fields map to which
    Nautobot API endpoints and fields.
    """

    # Device mappings (AVD -> Nautobot DCIM Device)
    device_mappings: list[FieldMapping] = field(
        default_factory=lambda: [
            FieldMapping("hostname", "name"),
            FieldMapping("metadata.serial_number", "serial"),
            FieldMapping("metadata.system_mac_address", "custom_fields.system_mac"),
        ]
    )

    # Interface mappings (AVD ethernet_interfaces -> Nautobot DCIM Interface)
    interface_mappings: list[FieldMapping] = field(
        default_factory=lambda: [
            FieldMapping("name", "name"),
            FieldMapping("description", "description"),
            FieldMapping("shutdown", "enabled", transform="invert_bool"),
            FieldMapping("mtu", "mtu"),
            FieldMapping("mode", "mode", transform="map_interface_mode"),
            FieldMapping("speed", "speed", transform="parse_speed"),
            FieldMapping("type", "type", transform="map_interface_type"),
        ]
    )

    # VLAN mappings (AVD vlans -> Nautobot IPAM VLAN)
    vlan_mappings: list[FieldMapping] = field(
        default_factory=lambda: [
            FieldMapping("id", "vid"),
            FieldMapping("name", "name"),
        ]
    )

    # VRF mappings (AVD vrfs -> Nautobot IPAM VRF)
    vrf_mappings: list[FieldMapping] = field(
        default_factory=lambda: [
            FieldMapping("name", "name"),
            FieldMapping("description", "description"),
        ]
    )

    @staticmethod
    def get_nautobot_endpoints() -> dict[str, str]:
        """Return Nautobot API endpoints for each model type."""
        return {
            "devices": "/api/dcim/devices/",
            "interfaces": "/api/dcim/interfaces/",
            "ip_addresses": "/api/ipam/ip-addresses/",
            "ip_to_interface": "/api/ipam/ip-address-to-interface/",
            "vlans": "/api/ipam/vlans/",
            "vrfs": "/api/ipam/vrfs/",
            "prefixes": "/api/ipam/prefixes/",
            "namespaces": "/api/ipam/namespaces/",
            "locations": "/api/dcim/locations/",
            "location_types": "/api/dcim/location-types/",
            "roles": "/api/extras/roles/",
            "device_types": "/api/dcim/device-types/",
            "manufacturers": "/api/dcim/manufacturers/",
            "platforms": "/api/dcim/platforms/",
            "cables": "/api/dcim/cables/",
            "tags": "/api/extras/tags/",
            "statuses": "/api/extras/statuses/",
        }


# AVD node type to Nautobot device role mapping
NODE_TYPE_TO_DEVICE_ROLE: dict[str, str] = {
    "spine": "spine",
    "l3leaf": "leaf",
    "l2leaf": "leaf",
    "super_spine": "super-spine",
    "overlay_controller": "overlay-controller",
    "wan_router": "wan-router",
    "pe": "pe-router",
    "p": "p-router",
    "rr": "route-reflector",
    "l3spine": "l3-spine",
    "leaf": "leaf",
    "l2spine": "l2-spine",
}


# AVD interface mode to Nautobot mode mapping
INTERFACE_MODE_MAP: dict[str, str] = {
    "access": "access",
    "trunk": "tagged",
    "dot1q-tunnel": "tagged",
    "trunk phone": "tagged",
}


# AVD VLAN state to Nautobot status mapping
VLAN_STATUS_MAP: dict[str, str] = {
    "active": "Active",
    "suspend": "Deprecated",
}


# Default values for Nautobot objects
DEFAULT_MANUFACTURER: dict[str, str] = {"name": "Arista"}
DEFAULT_DEVICE_TYPE: dict[str, str] = {"model": "vEOS"}
DEFAULT_PLATFORM: dict[str, str] = {"name": "EOS"}
DEFAULT_NAMESPACE: dict[str, str] = {"name": "Global"}
DEFAULT_LOCATION_TYPE: dict[str, str] = {"name": "Site"}


@dataclass
class SyncResult:
    """Result of a sync operation."""

    created: int = 0
    updated: int = 0
    skipped: int = 0
    deleted: int = 0
    errors: list[str] = field(default_factory=list)

    def __add__(self, other: SyncResult) -> SyncResult:
        return SyncResult(
            created=self.created + other.created,
            updated=self.updated + other.updated,
            skipped=self.skipped + other.skipped,
            deleted=self.deleted + other.deleted,
            errors=self.errors + other.errors,
        )
