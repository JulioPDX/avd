# Copyright (c) 2023-2026 Arista Networks, Inc.
# Use of this source code is governed by the Apache License 2.0
# that can be found in the LICENSE file.
"""
AVD to NetBox Model Mappings.

Defines the mapping between AVD structured config fields and NetBox API models.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FieldMapping:
    """Mapping between an AVD field and a NetBox field."""

    avd_path: str  # Dot-notation path in AVD structured config
    netbox_field: str  # NetBox API field name
    transform: str | None = None  # Optional transformation function name


@dataclass
class AVDNetBoxMapping:
    """
    Complete mapping configuration between AVD and NetBox models.

    This class defines which AVD structured config fields map to which
    NetBox API endpoints and fields.
    """

    # Device mappings (AVD -> NetBox DCIM Device)
    device_mappings: list[FieldMapping] = field(
        default_factory=lambda: [
            FieldMapping("hostname", "name"),
            FieldMapping("metadata.platform", "platform.slug", transform="slugify"),
            FieldMapping("metadata.serial_number", "serial"),
            FieldMapping("metadata.system_mac_address", "custom_fields.system_mac"),
        ]
    )

    # Interface mappings (AVD ethernet_interfaces -> NetBox DCIM Interface)
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

    # VLAN mappings (AVD vlans -> NetBox IPAM VLAN)
    vlan_mappings: list[FieldMapping] = field(
        default_factory=lambda: [
            FieldMapping("id", "vid"),
            FieldMapping("name", "name"),
            FieldMapping("state", "status", transform="map_vlan_status"),
        ]
    )

    # VRF mappings (AVD vrfs -> NetBox IPAM VRF)
    vrf_mappings: list[FieldMapping] = field(
        default_factory=lambda: [
            FieldMapping("name", "name"),
            FieldMapping("description", "description"),
            # rd (route distinguisher) mapped if present
        ]
    )

    @staticmethod
    def get_netbox_endpoints() -> dict[str, str]:
        """Return NetBox API endpoints for each model type."""
        return {
            "devices": "/api/dcim/devices/",
            "interfaces": "/api/dcim/interfaces/",
            "ip_addresses": "/api/ipam/ip-addresses/",
            "vlans": "/api/ipam/vlans/",
            "vrfs": "/api/ipam/vrfs/",
            "prefixes": "/api/ipam/prefixes/",
            "asns": "/api/ipam/asns/",
            "rirs": "/api/ipam/rirs/",
            "sites": "/api/dcim/sites/",
            "device_roles": "/api/dcim/device-roles/",
            "device_types": "/api/dcim/device-types/",
            "manufacturers": "/api/dcim/manufacturers/",
            "platforms": "/api/dcim/platforms/",
            "cables": "/api/dcim/cables/",
            "tags": "/api/extras/tags/",
        }


# AVD node type to NetBox device role mapping
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


# AVD interface mode to NetBox mode mapping
INTERFACE_MODE_MAP: dict[str, str] = {
    "access": "access",
    "trunk": "tagged",
    "dot1q-tunnel": "tagged",
    "trunk phone": "tagged",
}


# AVD VLAN state to NetBox status mapping
VLAN_STATUS_MAP: dict[str, str] = {
    "active": "active",
    "suspend": "deprecated",
}
