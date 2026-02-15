# Copyright (c) 2023-2026 Arista Networks, Inc.
# Use of this source code is governed by the Apache License 2.0
# that can be found in the LICENSE file.
# pylint: disable=too-many-lines
"""
AVD to NetBox Synchronization Logic.

Provides bidirectional synchronization between AVD structured configuration data and NetBox.
This module is intentionally kept as a single file to maintain cohesion between
AVD-to-NetBox and NetBox-to-AVD sync logic, which share common infrastructure.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .models import NODE_TYPE_TO_DEVICE_ROLE, AVDNetBoxMapping
from .transforms import apply_transform, get_nested_value, set_nested_value, slugify

if TYPE_CHECKING:
    from collections.abc import Sequence

    from .client import NetBoxClient

LOGGER = logging.getLogger(__name__)

# Default manufacturer for Arista devices
DEFAULT_MANUFACTURER = {"name": "Arista", "slug": "arista"}
DEFAULT_DEVICE_TYPE = {"model": "vEOS", "slug": "veos"}

# AVD node type categories for topology detection
SPINE_TYPES = {"spine", "l2spine", "l3spine", "super_spine"}
LEAF_TYPES = {"l3leaf", "l2leaf", "leaf"}
MPLS_TYPES = {"p", "pe", "rr"}
WAN_TYPES = {"wan_rr", "wan_router"}
ALL_NODE_TYPES = SPINE_TYPES | LEAF_TYPES | MPLS_TYPES | WAN_TYPES

# Underlay protocol detection from NetBox tags or device role patterns
UNDERLAY_PROTOCOL_MAP = {
    "isis": "isis",
    "ospf": "ospf",
    "ebgp": "ebgp",
    "ibgp": "ibgp",
    "none": "none",  # L2LS fabrics with no underlay routing
}

# Default interface patterns for different topologies
DEFAULT_INTERFACE_PATTERNS = {
    "l3ls": {
        "spine": {"uplink_interfaces": ["Ethernet1-2"], "downlink_interfaces": ["Ethernet1-8"]},
        "l3leaf": {"uplink_interfaces": ["Ethernet1-2"], "mlag_interfaces": ["Ethernet3-4"], "downlink_interfaces": ["Ethernet8"]},
        "l2leaf": {"uplink_interfaces": ["Ethernet1-2"]},
    },
    "l2ls": {
        "l2spine": {"uplink_interfaces": ["Ethernet1-2"], "mlag_interfaces": ["Ethernet47-48"], "downlink_interfaces": ["Ethernet1-8"]},
        "l2leaf": {"uplink_interfaces": ["Ethernet1-2"]},
    },
    "campus": {
        "l3spine": {"uplink_interfaces": ["Ethernet1-2"], "mlag_interfaces": ["Ethernet47-48"], "downlink_interfaces": ["Ethernet1-8"]},
        "l2leaf": {"uplink_interfaces": ["Ethernet1-2"]},
    },
}


@dataclass
class SyncResult:
    """Result of a sync operation."""

    created: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)

    def __add__(self, other: SyncResult) -> SyncResult:
        return SyncResult(
            created=self.created + other.created,
            updated=self.updated + other.updated,
            skipped=self.skipped + other.skipped,
            errors=self.errors + other.errors,
        )


class AVDNetBoxSync:
    """
    Synchronize AVD structured configuration to NetBox.

    Args:
        client: NetBox API client instance
        mapping: Field mapping configuration (uses defaults if not provided)
        dry_run: If True, don't make actual changes to NetBox
        site_name: Default NetBox site name for new objects
        create_prerequisites: If True, create missing sites/roles/types in NetBox
    """

    def __init__(
        self,
        client: NetBoxClient,
        mapping: AVDNetBoxMapping | None = None,
        *,
        dry_run: bool = False,
        site_name: str | None = None,
        create_prerequisites: bool = True,
    ) -> None:
        self.client = client
        self.mapping = mapping or AVDNetBoxMapping()
        self.dry_run = dry_run
        self.site_name = site_name
        self.create_prerequisites = create_prerequisites
        self._cache: dict[str, dict[str, Any]] = {}
        self._prerequisites_created = False

    def _get_or_cache(self, cache_key: str, endpoint: str, lookup_field: str) -> dict[str, Any]:
        """Get cached lookup table or fetch from NetBox."""
        if cache_key not in self._cache:
            self._cache[cache_key] = {}
            for obj in self.client.get_all(endpoint):
                key = get_nested_value(obj, lookup_field)
                if key:
                    self._cache[cache_key][key] = obj
        return self._cache[cache_key]

    def _ensure_prerequisites(self) -> None:
        """Ensure required NetBox objects exist (site, manufacturer, device type, roles)."""
        if self._prerequisites_created or not self.create_prerequisites or self.dry_run:
            return

        endpoints = self.mapping.get_netbox_endpoints()

        # Create manufacturer if needed
        manufacturer = self._find_netbox_object(endpoints["manufacturers"], slug=DEFAULT_MANUFACTURER["slug"])
        if not manufacturer:
            LOGGER.info("Creating manufacturer: %s", DEFAULT_MANUFACTURER["name"])
            manufacturer = self.client.post(endpoints["manufacturers"], DEFAULT_MANUFACTURER)
        self._cache["manufacturer"] = manufacturer

        # Create device type if needed
        device_type = self._find_netbox_object(endpoints["device_types"], slug=DEFAULT_DEVICE_TYPE["slug"])
        if not device_type:
            LOGGER.info("Creating device type: %s", DEFAULT_DEVICE_TYPE["model"])
            device_type_data = {**DEFAULT_DEVICE_TYPE, "manufacturer": manufacturer["id"]}
            device_type = self.client.post(endpoints["device_types"], device_type_data)
        self._cache["device_type"] = device_type

        # Create site if needed
        if self.site_name:
            site = self._find_netbox_object(endpoints["sites"], name=self.site_name)
            if not site:
                LOGGER.info("Creating site: %s", self.site_name)
                site = self.client.post(
                    endpoints["sites"],
                    {
                        "name": self.site_name,
                        "slug": slugify(self.site_name),
                        "status": "active",
                    },
                )
            self._cache["site"] = site

        # Create device roles for AVD node types
        for role_slug in NODE_TYPE_TO_DEVICE_ROLE.values():
            role = self._find_netbox_object(endpoints["device_roles"], slug=role_slug)
            if not role:
                LOGGER.info("Creating device role: %s", role_slug)
                self.client.post(
                    endpoints["device_roles"],
                    {
                        "name": role_slug.replace("-", " ").title(),
                        "slug": role_slug,
                    },
                )

        self._prerequisites_created = True

    def _find_netbox_object(self, endpoint: str, **filters: Any) -> dict[str, Any] | None:
        """Find a single object in NetBox by filters."""
        result = self.client.get(endpoint, params=filters)
        if result and result.get("results"):
            return result["results"][0]
        return None

    def _infer_node_type_from_hostname(self, hostname: str) -> str | None:
        """
        Infer AVD node type from hostname patterns.

        Common patterns:
        - *spine* -> spine (or l2spine/l3spine variants)
        - *leaf*[0-9][a-b] -> l3leaf (paired leafs)
        - *leaf*[0-9]c -> l2leaf (standalone leafs)
        - *pe*, *rr*, p[0-9]* -> MPLS types
        - *wan* -> WAN types
        """
        hostname_lower = hostname.lower()
        node_type: str | None = None

        # Check for spine patterns
        if "spine" in hostname_lower:
            if "l2spine" in hostname_lower or "l2-spine" in hostname_lower:
                node_type = "l2spine"
            elif "l3spine" in hostname_lower or "l3-spine" in hostname_lower:
                node_type = "l3spine"
            else:
                node_type = "spine"
        # Check for leaf patterns
        elif "leaf" in hostname_lower:
            # L2 leafs often end with 'c' (e.g., dc1-leaf1c)
            node_type = "l2leaf" if hostname_lower.endswith("c") else "l3leaf"
        # Check for MPLS node types
        elif "-pe" in hostname_lower or hostname_lower.startswith("pe"):
            node_type = "pe"
        elif "-rr" in hostname_lower or hostname_lower.startswith("rr"):
            node_type = "rr"
        # P router - careful not to match other names
        elif hostname_lower.startswith("p") and len(hostname_lower) >= 2 and hostname_lower[1].isdigit():
            node_type = "p"
        # Check for WAN types
        elif "wan-rr" in hostname_lower or "wan_rr" in hostname_lower:
            node_type = "wan_rr"
        elif "wan" in hostname_lower:
            node_type = "wan_router"

        return node_type

    def _transform_avd_to_netbox(
        self,
        avd_data: dict[str, Any],
        mappings: Sequence,
    ) -> dict[str, Any]:
        """Transform AVD data to NetBox format using mappings."""
        netbox_data: dict[str, Any] = {}

        for mapping in mappings:
            value = get_nested_value(avd_data, mapping.avd_path)
            if value is None:
                continue

            # Apply transformation if specified
            if mapping.transform:
                value = apply_transform(mapping.transform, value)

            set_nested_value(netbox_data, mapping.netbox_field, value)

        return netbox_data

    def sync_device(self, avd_structured_config: dict[str, Any], node_type: str | None = None) -> SyncResult:
        """
        Sync a single device from AVD structured config to NetBox.

        Args:
            avd_structured_config: AVD structured configuration for a device
            node_type: AVD node type (spine, l3leaf, etc.) for device role mapping

        Returns:
            SyncResult with operation counts
        """
        result = SyncResult()
        hostname = avd_structured_config.get("hostname")

        if not hostname:
            result.errors.append("Device missing hostname")
            return result

        # Ensure prerequisites exist
        self._ensure_prerequisites()

        LOGGER.info("Syncing device: %s", hostname)

        endpoints = self.mapping.get_netbox_endpoints()

        # Transform AVD data to NetBox format
        netbox_data = self._transform_avd_to_netbox(avd_structured_config, self.mapping.device_mappings)

        # Handle platform lookup - if we have a nested platform object with slug, look it up
        if "platform" in netbox_data and isinstance(netbox_data["platform"], dict):
            platform_slug = netbox_data["platform"].get("slug")
            # Remove the nested object since NetBox expects an ID
            del netbox_data["platform"]
            if platform_slug:
                # Look up the platform by slug
                platform = self._find_netbox_object(endpoints["platforms"], slug=platform_slug)
                if platform:
                    netbox_data["platform"] = platform["id"]
                else:
                    LOGGER.debug("Platform '%s' not found in NetBox, skipping platform assignment", platform_slug)

        # Add device role from node type - need to look up the role ID
        # If node_type not provided, try to infer from hostname
        if not node_type:
            node_type = self._infer_node_type_from_hostname(hostname)

        if node_type:
            role_slug = NODE_TYPE_TO_DEVICE_ROLE.get(node_type)
            if role_slug:
                role = self._find_netbox_object(endpoints["device_roles"], slug=role_slug)
                if role:
                    netbox_data["role"] = role["id"]

        # If still no role, use a default role to avoid NetBox 400 error
        if "role" not in netbox_data:
            # Try to use "leaf" as default since it's the most common
            role = self._find_netbox_object(endpoints["device_roles"], slug="leaf")
            if role:
                netbox_data["role"] = role["id"]
            else:
                LOGGER.warning("No device role found for %s, device creation may fail", hostname)

        # Set device status to active (NetBox default status values)
        netbox_data["status"] = "active"

        # Check if device exists
        existing = self._find_netbox_object(endpoints["devices"], name=hostname)

        if self.dry_run:
            action = "update" if existing else "create"
            LOGGER.info("[DRY RUN] Would %s device: %s", action, hostname)
            result.skipped += 1
            return result

        try:
            if existing:
                self.client.patch(f"{endpoints['devices']}{existing['id']}/", netbox_data)
                result.updated += 1
                LOGGER.debug("Updated device: %s", hostname)
            else:
                # Add required fields for new device
                if self.site_name and "site" in self._cache:
                    netbox_data["site"] = self._cache["site"]["id"]
                if "device_type" in self._cache:
                    netbox_data["device_type"] = self._cache["device_type"]["id"]
                LOGGER.debug("Creating device with data: %s", netbox_data)
                self.client.post(endpoints["devices"], netbox_data)
                result.created += 1
                LOGGER.debug("Created device: %s", hostname)
        except Exception as e:
            error_msg = f"Failed to sync device {hostname}: {e}"
            result.errors.append(error_msg)
            LOGGER.exception("Failed to sync device %s", hostname)

        return result

    def sync_interfaces(self, avd_structured_config: dict[str, Any]) -> SyncResult:
        """
        Sync interfaces from AVD structured config to NetBox.

        Args:
            avd_structured_config: AVD structured configuration for a device

        Returns:
            SyncResult with operation counts
        """
        result = SyncResult()
        hostname = avd_structured_config.get("hostname")
        endpoints = self.mapping.get_netbox_endpoints()

        # Find device in NetBox
        device = self._find_netbox_object(endpoints["devices"], name=hostname)
        if not device:
            result.errors.append(f"Device {hostname} not found in NetBox")
            return result

        device_id = device["id"]

        # Process all interface types
        interface_types = [
            "ethernet_interfaces",
            "loopback_interfaces",
            "vlan_interfaces",
            "port_channel_interfaces",
            "management_interfaces",
        ]

        for intf_type in interface_types:
            interfaces = avd_structured_config.get(intf_type, [])
            for intf in interfaces:
                intf_result = self._sync_single_interface(intf, device_id, endpoints)
                result = result + intf_result

        return result

    def _sync_single_interface(self, intf_data: dict[str, Any], device_id: int, endpoints: dict[str, str]) -> SyncResult:
        """Sync a single interface to NetBox."""
        result = SyncResult()
        intf_name = intf_data.get("name")

        if not intf_name:
            result.errors.append("Interface missing name")
            return result

        netbox_data = self._transform_avd_to_netbox(intf_data, self.mapping.interface_mappings)
        netbox_data["device"] = device_id

        # Set interface type based on name
        netbox_data["type"] = apply_transform("map_interface_type", intf_name)

        # Handle VRF assignment - look up VRF ID by name
        if vrf_name := intf_data.get("vrf"):
            vrf = self._find_netbox_object(endpoints["vrfs"], name=vrf_name)
            if vrf:
                netbox_data["vrf"] = vrf["id"]
            else:
                LOGGER.debug("VRF '%s' not found for interface %s", vrf_name, intf_name)

        existing = self._find_netbox_object(endpoints["interfaces"], device_id=device_id, name=intf_name)

        if self.dry_run:
            result.skipped += 1
            return result

        try:
            created_intf = None
            if existing:
                self.client.patch(f"{endpoints['interfaces']}{existing['id']}/", netbox_data)
                result.updated += 1
                created_intf = existing
            else:
                created_intf = self.client.post(endpoints["interfaces"], netbox_data)
                result.created += 1

            # Use the interface with ID for IP assignment
            intf_for_ip = created_intf or existing

            # Sync IP addresses if we have a valid interface
            if intf_for_ip:
                # Sync IP address if present (regular IP)
                if ip_addr := intf_data.get("ip_address"):
                    self._sync_interface_ip(ip_addr, intf_for_ip, endpoints)

                # Sync virtual IP address if present (anycast gateway IPs on SVIs)
                if virtual_ip := intf_data.get("ip_address_virtual"):
                    self._sync_interface_ip(virtual_ip, intf_for_ip, endpoints, role="anycast")

        except Exception as e:
            result.errors.append(f"Failed to sync interface {intf_name}: {e}")

        return result

    def _sync_interface_ip(self, ip_address: str, interface: dict[str, Any], endpoints: dict[str, str], role: str | None = None) -> dict[str, Any] | None:
        """
        Sync IP address for an interface.

        Args:
            ip_address: IP address in CIDR notation
            interface: NetBox interface dict (must have 'id')
            endpoints: NetBox API endpoints
            role: Optional IP role (e.g., 'anycast' for virtual IPs)

        Returns:
            Created/updated IP address object dict, or None if failed
        """
        if self.dry_run:
            return None

        intf_id = interface.get("id")
        if not intf_id:
            return None

        existing_ip = self._find_netbox_object(
            endpoints["ip_addresses"],
            address=ip_address,
        )

        ip_data: dict[str, Any] = {
            "address": ip_address,
            "assigned_object_type": "dcim.interface",
            "assigned_object_id": intf_id,
        }

        # Add role if specified (e.g., "anycast" for virtual IPs)
        if role:
            ip_data["role"] = role

        try:
            if existing_ip:
                self.client.patch(f"{endpoints['ip_addresses']}{existing_ip['id']}/", ip_data)
                return existing_ip
            return self.client.post(endpoints["ip_addresses"], ip_data)
        except Exception as e:
            LOGGER.warning("Failed to sync IP %s: %s", ip_address, e)
            return None

    def sync_vlans(self, avd_structured_config: dict[str, Any]) -> SyncResult:
        """Sync VLANs from AVD structured config to NetBox."""
        result = SyncResult()
        vlans = avd_structured_config.get("vlans", [])
        endpoints = self.mapping.get_netbox_endpoints()

        for vlan in vlans:
            vlan_id = vlan.get("id")
            if not vlan_id:
                continue

            netbox_data = self._transform_avd_to_netbox(vlan, self.mapping.vlan_mappings)

            if self.site_name:
                site = self._find_netbox_object(endpoints["sites"], name=self.site_name)
                if site:
                    netbox_data["site"] = site["id"]

            existing = self._find_netbox_object(endpoints["vlans"], vid=vlan_id)

            if self.dry_run:
                result.skipped += 1
                continue

            try:
                if existing:
                    self.client.patch(f"{endpoints['vlans']}{existing['id']}/", netbox_data)
                    result.updated += 1
                else:
                    self.client.post(endpoints["vlans"], netbox_data)
                    result.created += 1
            except Exception as e:
                result.errors.append(f"Failed to sync VLAN {vlan_id}: {e}")

        return result

    def sync_vrfs(self, avd_structured_config: dict[str, Any]) -> SyncResult:
        """Sync VRFs from AVD structured config to NetBox."""
        result = SyncResult()
        vrfs = avd_structured_config.get("vrfs", [])
        endpoints = self.mapping.get_netbox_endpoints()

        for vrf in vrfs:
            vrf_name = vrf.get("name")
            if not vrf_name or vrf_name == "default":
                continue

            netbox_data = self._transform_avd_to_netbox(vrf, self.mapping.vrf_mappings)
            existing = self._find_netbox_object(endpoints["vrfs"], name=vrf_name)

            if self.dry_run:
                result.skipped += 1
                continue

            try:
                if existing:
                    self.client.patch(f"{endpoints['vrfs']}{existing['id']}/", netbox_data)
                    result.updated += 1
                else:
                    self.client.post(endpoints["vrfs"], netbox_data)
                    result.created += 1
            except Exception as e:
                result.errors.append(f"Failed to sync VRF {vrf_name}: {e}")

        return result

    def sync_primary_ip(self, avd_structured_config: dict[str, Any]) -> SyncResult:
        """
        Set the device's primary IP from management interface.

        Looks for Management1 interface IP and sets it as the device's primary_ip4.

        Args:
            avd_structured_config: AVD structured configuration for a device

        Returns:
            SyncResult with operation counts
        """
        result = SyncResult()
        hostname = avd_structured_config.get("hostname")
        endpoints = self.mapping.get_netbox_endpoints()

        if not hostname:
            return result

        # Get management interfaces
        mgmt_interfaces = avd_structured_config.get("management_interfaces", [])
        if not mgmt_interfaces:
            return result

        # Find the first management interface with an IP
        mgmt_ip_address = None
        for mgmt_intf in mgmt_interfaces:
            if ip_addr := mgmt_intf.get("ip_address"):
                mgmt_ip_address = ip_addr
                break

        if not mgmt_ip_address:
            return result

        if self.dry_run:
            LOGGER.info("[DRY RUN] Would set primary_ip4 for %s to %s", hostname, mgmt_ip_address)
            return result

        try:
            # Find the device
            device = self._find_netbox_object(endpoints["devices"], name=hostname)
            if not device:
                return result

            # Find the IP address object in NetBox
            ip_obj = self._find_netbox_object(endpoints["ip_addresses"], address=mgmt_ip_address)
            if not ip_obj:
                LOGGER.debug("IP %s not found in NetBox for primary_ip4 assignment", mgmt_ip_address)
                return result

            # Update device with primary IP
            self.client.patch(f"{endpoints['devices']}{device['id']}/", {"primary_ip4": ip_obj["id"]})
            result.updated += 1
            LOGGER.info("Set primary_ip4 for %s to %s", hostname, mgmt_ip_address)

        except Exception as e:
            result.errors.append(f"Failed to set primary IP for {hostname}: {e}")
            LOGGER.warning("Failed to set primary IP for %s: %s", hostname, e)

        return result

    def sync_all(
        self,
        avd_structured_configs: dict[str, dict[str, Any]],
        node_types: dict[str, str] | None = None,
    ) -> SyncResult:
        """
        Sync all devices and their data from AVD structured configs.

        Args:
            avd_structured_configs: Dict mapping hostname to structured config
            node_types: Optional dict mapping hostname to AVD node type

        Returns:
            Combined SyncResult for all operations
        """
        result = SyncResult()
        node_types = node_types or {}

        # First pass: sync VRFs so they exist for interface VRF assignments
        for config in avd_structured_configs.values():
            result = result + self.sync_vrfs(config)

        # Second pass: sync devices and interfaces
        for hostname, config in avd_structured_configs.items():
            LOGGER.info("Syncing device %s to NetBox", hostname)
            node_type = node_types.get(hostname)

            # Sync device first
            result = result + self.sync_device(config, node_type)

            # Then sync related objects (interfaces need VRFs to exist already)
            result = result + self.sync_interfaces(config)
            result = result + self.sync_vlans(config)

        # Third pass: set primary IPs after interfaces and IPs exist
        for config in avd_structured_configs.values():
            result = result + self.sync_primary_ip(config)

        # Sync cables after all devices and interfaces exist
        result = result + self.sync_cables(avd_structured_configs)

        LOGGER.info(
            "Sync complete: %d created, %d updated, %d skipped, %d errors",
            result.created,
            result.updated,
            result.skipped,
            len(result.errors),
        )

        return result

    def sync_cables(self, avd_structured_configs: dict[str, dict[str, Any]]) -> SyncResult:
        """
        Sync cable connections from AVD interface metadata to NetBox.

        Uses the metadata.peer and metadata.peer_interface fields from AVD
        ethernet_interfaces to create cables in NetBox.

        Args:
            avd_structured_configs: Dict mapping hostname to structured config

        Returns:
            SyncResult with operation counts
        """
        result = SyncResult()
        endpoints = self.mapping.get_netbox_endpoints()
        processed_pairs: set[tuple[str, str, str, str]] = set()

        for hostname, config in avd_structured_configs.items():
            for intf in config.get("ethernet_interfaces", []):
                metadata = intf.get("metadata", {})
                peer = metadata.get("peer")
                peer_interface = metadata.get("peer_interface")
                intf_name = intf.get("name")

                if not all([peer, peer_interface, intf_name]):
                    continue

                # Create a sorted pair to avoid duplicate cable creation
                pair = tuple(sorted([(hostname, intf_name), (peer, peer_interface)]))
                pair_key = (pair[0][0], pair[0][1], pair[1][0], pair[1][1])

                if pair_key in processed_pairs:
                    continue
                processed_pairs.add(pair_key)

                cable_result = self._sync_single_cable(hostname, intf_name, peer, peer_interface, endpoints)
                result = result + cable_result

        return result

    def _sync_single_cable(
        self,
        device_a: str,
        interface_a: str,
        device_b: str,
        interface_b: str,
        endpoints: dict[str, str],
    ) -> SyncResult:
        """Sync a single cable between two interfaces."""
        result = SyncResult()

        # Find both interfaces in NetBox
        device_a_obj = self._find_netbox_object(endpoints["devices"], name=device_a)
        device_b_obj = self._find_netbox_object(endpoints["devices"], name=device_b)

        if not device_a_obj or not device_b_obj:
            LOGGER.debug("Skipping cable: device not found (%s or %s)", device_a, device_b)
            result.skipped += 1
            return result

        intf_a = self._find_netbox_object(endpoints["interfaces"], device_id=device_a_obj["id"], name=interface_a)
        intf_b = self._find_netbox_object(endpoints["interfaces"], device_id=device_b_obj["id"], name=interface_b)

        if not intf_a or not intf_b:
            LOGGER.debug(
                "Skipping cable: interface not found (%s:%s or %s:%s)",
                device_a,
                interface_a,
                device_b,
                interface_b,
            )
            result.skipped += 1
            return result

        # Check if cable already exists
        existing_cable = self._find_netbox_object(
            endpoints["cables"],
            termination_a_id=intf_a["id"],
            termination_a_type="dcim.interface",
        )

        if existing_cable:
            result.skipped += 1
            return result

        if self.dry_run:
            LOGGER.info(
                "[DRY RUN] Would create cable: %s:%s <-> %s:%s",
                device_a,
                interface_a,
                device_b,
                interface_b,
            )
            result.skipped += 1
            return result

        try:
            cable_data = {
                "a_terminations": [{"object_type": "dcim.interface", "object_id": intf_a["id"]}],
                "b_terminations": [{"object_type": "dcim.interface", "object_id": intf_b["id"]}],
                "status": "connected",
            }
            self.client.post(endpoints["cables"], cable_data)
            result.created += 1
            LOGGER.debug(
                "Created cable: %s:%s <-> %s:%s",
                device_a,
                interface_a,
                device_b,
                interface_b,
            )
        except Exception as e:
            result.errors.append(f"Failed to create cable {device_a}:{interface_a} <-> {device_b}:{interface_b}: {e}")

        return result
