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
        if node_type:
            role_slug = NODE_TYPE_TO_DEVICE_ROLE.get(node_type)
            if role_slug:
                role = self._find_netbox_object(endpoints["device_roles"], slug=role_slug)
                if role:
                    netbox_data["role"] = role["id"]

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

    # =========================================================================
    # NetBox to AVD Sync Methods
    # =========================================================================

    def fetch_devices_from_netbox(self, site_name: str | None = None) -> dict[str, dict[str, Any]]:
        """
        Fetch devices from NetBox and convert to AVD-compatible inventory data.

        Args:
            site_name: Optional site filter

        Returns:
            Dict mapping hostname to inventory data suitable for AVD
        """
        endpoints = self.mapping.get_netbox_endpoints()
        params = {}
        if site_name:
            params["site"] = site_name

        inventory: dict[str, dict[str, Any]] = {}

        for device in self.client.get_all(endpoints["devices"], params):
            hostname = device.get("name")
            if not hostname:
                continue

            # Build AVD-compatible inventory data
            inventory[hostname] = {
                "ansible_host": self._get_device_mgmt_ip(device),
                "type": self._netbox_role_to_avd_type(device),
                "metadata": {
                    "netbox_id": device.get("id"),
                    "platform": device.get("platform", {}).get("slug") if device.get("platform") else None,
                    "serial_number": device.get("serial"),
                    "site": device.get("site", {}).get("name") if device.get("site") else None,
                },
            }

        return inventory

    def _get_device_mgmt_ip(self, device: dict[str, Any]) -> str | None:
        """Get management IP address for a device from NetBox."""
        primary_ip = device.get("primary_ip")
        if primary_ip:
            # Extract IP without prefix length
            address = primary_ip.get("address", "")
            return address.split("/")[0] if "/" in address else address
        return None

    def _netbox_role_to_avd_type(self, device: dict[str, Any]) -> str | None:
        """Map NetBox device role to AVD node type."""
        role = device.get("role", {})
        if not role:
            return None

        role_slug = role.get("slug", "")

        # Reverse mapping from NODE_TYPE_TO_DEVICE_ROLE
        avd_type_map = {v: k for k, v in NODE_TYPE_TO_DEVICE_ROLE.items()}
        return avd_type_map.get(role_slug)

    def fetch_vlans_from_netbox(self, site_name: str | None = None) -> list[dict[str, Any]]:
        """
        Fetch VLANs from NetBox and convert to AVD format.

        Returns:
            List of VLAN dicts in AVD vlans format
        """
        endpoints = self.mapping.get_netbox_endpoints()
        params = {}
        if site_name:
            params["site"] = site_name

        return [
            {
                "id": vlan.get("vid"),
                "name": vlan.get("name"),
                "state": "active" if vlan.get("status", {}).get("value") == "active" else "suspend",
            }
            for vlan in self.client.get_all(endpoints["vlans"], params)
        ]

    def fetch_vrfs_from_netbox(self) -> list[dict[str, Any]]:
        """
        Fetch VRFs from NetBox and convert to AVD format.

        Returns:
            List of VRF dicts in AVD vrfs format
        """
        endpoints = self.mapping.get_netbox_endpoints()
        return [
            {
                "name": vrf.get("name"),
                "description": vrf.get("description"),
                "rd": vrf.get("rd"),
            }
            for vrf in self.client.get_all(endpoints["vrfs"])
        ]

    def fetch_prefixes_from_netbox(self, vrf_name: str | None = None) -> list[dict[str, Any]]:
        """
        Fetch IP prefixes from NetBox.

        Args:
            vrf_name: Optional VRF filter

        Returns:
            List of prefix dicts
        """
        endpoints = self.mapping.get_netbox_endpoints()
        params = {}
        if vrf_name:
            params["vrf"] = vrf_name

        return [
            {
                "prefix": prefix.get("prefix"),
                "vrf": prefix.get("vrf", {}).get("name") if prefix.get("vrf") else None,
                "description": prefix.get("description"),
                "role": prefix.get("role", {}).get("slug") if prefix.get("role") else None,
            }
            for prefix in self.client.get_all(endpoints["prefixes"], params)
        ]

    def generate_avd_inventory(self, site_name: str | None = None) -> dict[str, Any]:
        """
        Generate a complete AVD-compatible inventory from NetBox data.

        This creates inventory data that can be used to bootstrap an AVD deployment.

        Args:
            site_name: NetBox site to fetch devices from

        Returns:
            Dict with AVD inventory structure
        """
        devices = self.fetch_devices_from_netbox(site_name)

        # Group devices by type
        spines = {}
        l3leafs = {}
        l2leafs = {}
        other = {}

        for hostname, data in devices.items():
            device_type = data.get("type")
            if device_type == "spine":
                spines[hostname] = data
            elif device_type in ("l3leaf", "leaf"):
                l3leafs[hostname] = data
            elif device_type == "l2leaf":
                l2leafs[hostname] = data
            else:
                other[hostname] = data

        inventory = {"all": {"children": {"FABRIC": {"children": {}}}}}

        fabric_children = inventory["all"]["children"]["FABRIC"]["children"]

        if spines:
            fabric_children["SPINES"] = {"hosts": spines}
        if l3leafs:
            fabric_children["L3_LEAFS"] = {"hosts": l3leafs}
        if l2leafs:
            fabric_children["L2_LEAFS"] = {"hosts": l2leafs}
        if other:
            fabric_children["OTHER"] = {"hosts": other}

        return inventory

    def fetch_cables_from_netbox(self, site_name: str | None = None) -> list[dict[str, Any]]:
        """
        Fetch cables from NetBox and convert to AVD uplink/peer format.

        Args:
            site_name: Optional site filter (slug format)

        Returns:
            List of cable connections in AVD-compatible format
        """
        endpoints = self.mapping.get_netbox_endpoints()
        params = {}
        if site_name:
            params["site"] = site_name

        connections: list[dict[str, Any]] = []

        for cable in self.client.get_all(endpoints["cables"], params):
            # NetBox cable has a_terminations and b_terminations (lists)
            a_terms = cable.get("a_terminations", [])
            b_terms = cable.get("b_terminations", [])

            if not a_terms or not b_terms:
                continue

            # Get first termination from each side (most cables are point-to-point)
            a_term = a_terms[0].get("object", {})
            b_term = b_terms[0].get("object", {})

            # Extract device and interface info
            a_device = a_term.get("device", {}).get("name") if a_term.get("device") else None
            a_interface = a_term.get("name")
            b_device = b_term.get("device", {}).get("name") if b_term.get("device") else None
            b_interface = b_term.get("name")

            if all([a_device, a_interface, b_device, b_interface]):
                connections.append(
                    {
                        "cable_id": cable.get("id"),
                        "a_device": a_device,
                        "a_interface": a_interface,
                        "b_device": b_device,
                        "b_interface": b_interface,
                        "status": cable.get("status", {}).get("value"),
                        "type": cable.get("type"),
                        "label": cable.get("label"),
                    }
                )

        return connections

    def fetch_ip_addresses_from_netbox(self, site_name: str | None = None, device_name: str | None = None) -> list[dict[str, Any]]:
        """
        Fetch IP addresses from NetBox with interface assignments.

        Args:
            site_name: Optional site filter (slug format)
            device_name: Optional device filter

        Returns:
            List of IP address dicts with device/interface info
        """
        endpoints = self.mapping.get_netbox_endpoints()
        params = {}
        if device_name:
            params["device"] = device_name

        ip_addresses: list[dict[str, Any]] = []

        for ip in self.client.get_all(endpoints["ip_addresses"], params):
            assigned_obj = ip.get("assigned_object")
            if not assigned_obj:
                continue

            device = assigned_obj.get("device", {})
            device_site = device.get("site", {}).get("slug") if device.get("site") else None

            # Filter by site if specified
            if site_name and device_site != site_name:
                continue

            ip_addresses.append(
                {
                    "address": ip.get("address"),
                    "device": device.get("name") if device else None,
                    "interface": assigned_obj.get("name"),
                    "vrf": ip.get("vrf", {}).get("name") if ip.get("vrf") else None,
                    "status": ip.get("status", {}).get("value"),
                    "role": ip.get("role", {}).get("value") if ip.get("role") else None,
                    "is_primary": ip.get("id") == device.get("primary_ip4", {}).get("id") if device.get("primary_ip4") else False,
                    "dns_name": ip.get("dns_name"),
                }
            )

        return ip_addresses

    def _build_uplink_topology(self, connections: list[dict[str, Any]], devices: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        """Build uplink topology from cable connections."""
        uplinks: dict[str, dict[str, Any]] = {}

        for conn in connections:
            a_device = conn["a_device"]
            b_device = conn["b_device"]

            # Determine which device is upstream (spine) vs downstream (leaf)
            a_type = devices.get(a_device, {}).get("type")
            b_type = devices.get(b_device, {}).get("type")

            # If a is leaf and b is spine, a uplinks to b
            if a_type in ("leaf", "l3leaf", "l2leaf") and b_type == "spine":
                if a_device not in uplinks:
                    uplinks[a_device] = {"uplink_interfaces": [], "uplink_switches": [], "uplink_switch_interfaces": []}
                uplinks[a_device]["uplink_interfaces"].append(conn["a_interface"])
                uplinks[a_device]["uplink_switches"].append(b_device)
                uplinks[a_device]["uplink_switch_interfaces"].append(conn["b_interface"])

            # If b is leaf and a is spine, b uplinks to a
            elif b_type in ("leaf", "l3leaf", "l2leaf") and a_type == "spine":
                if b_device not in uplinks:
                    uplinks[b_device] = {"uplink_interfaces": [], "uplink_switches": [], "uplink_switch_interfaces": []}
                uplinks[b_device]["uplink_interfaces"].append(conn["b_interface"])
                uplinks[b_device]["uplink_switches"].append(a_device)
                uplinks[b_device]["uplink_switch_interfaces"].append(conn["a_interface"])

        # Second pass: detect L2 leaf to L3 leaf uplinks (L2 leafs connect to L3 leafs, not spines)
        # A device is likely L2 leaf if it only connects to other leafs, not spines
        devices_with_spine_uplinks = set(uplinks.keys())

        for conn in connections:
            a_device = conn["a_device"]
            b_device = conn["b_device"]

            a_type = devices.get(a_device, {}).get("type")
            b_type = devices.get(b_device, {}).get("type")

            # Both are leafs - determine which is L2 (no spine uplinks) and which is L3 (has spine uplinks)
            if a_type in ("leaf", "l3leaf", "l2leaf") and b_type in ("leaf", "l3leaf", "l2leaf"):
                # Skip MLAG peer links: both have "a" or "b" suffix with same base (e.g., leaf1a <-> leaf1b)
                # But don't skip L2 leaf connections (c suffix connecting to a/b)
                a_suffix = a_device[-1] if a_device else ""
                b_suffix = b_device[-1] if b_device else ""
                a_base = a_device.rstrip("abc")
                b_base = b_device.rstrip("abc")

                # MLAG pair: same base, one ends in 'a', one ends in 'b'
                is_mlag_pair = a_base == b_base and {a_suffix, b_suffix} == {"a", "b"}
                if is_mlag_pair:
                    continue

                # If a has no spine uplinks but b does, a uplinks to b (a is L2, b is L3)
                if a_device not in devices_with_spine_uplinks and b_device in devices_with_spine_uplinks:
                    if a_device not in uplinks:
                        uplinks[a_device] = {"uplink_interfaces": [], "uplink_switches": [], "uplink_switch_interfaces": []}
                    uplinks[a_device]["uplink_interfaces"].append(conn["a_interface"])
                    uplinks[a_device]["uplink_switches"].append(b_device)
                    uplinks[a_device]["uplink_switch_interfaces"].append(conn["b_interface"])

                # If b has no spine uplinks but a does, b uplinks to a (b is L2, a is L3)
                elif b_device not in devices_with_spine_uplinks and a_device in devices_with_spine_uplinks:
                    if b_device not in uplinks:
                        uplinks[b_device] = {"uplink_interfaces": [], "uplink_switches": [], "uplink_switch_interfaces": []}
                    uplinks[b_device]["uplink_interfaces"].append(conn["b_interface"])
                    uplinks[b_device]["uplink_switches"].append(a_device)
                    uplinks[b_device]["uplink_switch_interfaces"].append(conn["a_interface"])

        return uplinks

    def _build_mlag_topology(self, connections: list[dict[str, Any]], devices: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        """Build MLAG peer topology from cable connections."""
        mlag_peers: dict[str, dict[str, Any]] = {}

        for conn in connections:
            a_device = conn["a_device"]
            b_device = conn["b_device"]

            a_type = devices.get(a_device, {}).get("type")
            b_type = devices.get(b_device, {}).get("type")

            # MLAG peer link detection: same type devices with matching names (leaf1a <-> leaf1b)
            if a_type == b_type and a_type in ("leaf", "l3leaf"):
                # Check if names suggest MLAG pair (e.g., dc1-leaf1a and dc1-leaf1b)
                a_base = a_device.rstrip("ab")
                b_base = b_device.rstrip("ab")

                if a_base == b_base and a_device != b_device:
                    if a_device not in mlag_peers:
                        mlag_peers[a_device] = {"mlag_peer": b_device, "mlag_interfaces": []}
                    mlag_peers[a_device]["mlag_interfaces"].append(conn["a_interface"])

                    if b_device not in mlag_peers:
                        mlag_peers[b_device] = {"mlag_peer": a_device, "mlag_interfaces": []}
                    mlag_peers[b_device]["mlag_interfaces"].append(conn["b_interface"])

        return mlag_peers

    def generate_avd_group_vars(self, site_name: str | None = None) -> dict[str, Any]:
        """
        Generate complete AVD group_vars from NetBox data.

        This creates fabric variables, node type defaults, and network services
        suitable for populating AVD group_vars files.

        The generated structure follows AVD eos_designs schema requirements:
        - Separate files for spines, l3leafs, l2leafs, and network_services
        - All required fields for schema validation (uplink_interfaces, mlag_interfaces, bgp_as)
        - Proper node type classification

        Args:
            site_name: NetBox site to fetch data from (slug format)

        Returns:
            Dict with AVD group_vars structure, organized by file type
        """
        # Fetch all required data
        devices = self.fetch_devices_from_netbox(site_name)
        connections = self.fetch_cables_from_netbox(site_name)
        vlans = self.fetch_vlans_from_netbox(site_name)
        vrfs = self.fetch_vrfs_from_netbox()
        ip_addresses = self.fetch_ip_addresses_from_netbox(site_name)

        # Build topology from connections
        uplinks = self._build_uplink_topology(connections, devices)
        mlag_peers = self._build_mlag_topology(connections, devices)

        # Group devices by type - distinguish between l3leaf and l2leaf
        spines = {h: d for h, d in devices.items() if d.get("type") == "spine"}
        l3leafs: dict[str, dict[str, Any]] = {}
        l2leafs: dict[str, dict[str, Any]] = {}

        for hostname, device in devices.items():
            device_type = device.get("type", "")
            # Check if it's an L2 leaf (standalone, no MLAG pair, typically "c" suffix)
            if device_type in ("leaf", "l3leaf"):
                # Check if this device has uplinks to other leafs (L2 leaf pattern)
                uplink_info = uplinks.get(hostname, {})
                uplink_switches = uplink_info.get("uplink_switches", [])
                # If uplinks go to leafs, it's an L2 leaf
                is_l2_leaf = any(sw in devices and devices[sw].get("type") in ("leaf", "l3leaf") for sw in uplink_switches)
                if is_l2_leaf:
                    l2leafs[hostname] = device
                else:
                    l3leafs[hostname] = device
            elif device_type == "l2leaf":
                l2leafs[hostname] = device

        # Build IP address lookup by device/interface
        ip_lookup: dict[str, dict[str, str]] = {}
        for ip in ip_addresses:
            device = ip.get("device")
            interface = ip.get("interface")
            if device and interface:
                if device not in ip_lookup:
                    ip_lookup[device] = {}
                ip_lookup[device][interface] = ip.get("address", "")

        # Build fabric name from site
        fabric_name = f"{site_name.upper()}_FABRIC" if site_name else "FABRIC"

        # Generate FABRIC variables
        fabric_vars: dict[str, Any] = {
            "fabric_name": fabric_name,
            "underlay_routing_protocol": "ebgp",
            "overlay_routing_protocol": "ebgp",
            "p2p_uplinks_mtu": 1500,
            "default_interfaces": [
                {
                    "types": ["spine"],
                    "platforms": ["default"],
                    "uplink_interfaces": ["Ethernet1-2"],
                    "downlink_interfaces": ["Ethernet1-8"],
                },
                {
                    "types": ["l3leaf"],
                    "platforms": ["default"],
                    "uplink_interfaces": ["Ethernet1-2"],
                    "mlag_interfaces": ["Ethernet3-4"],
                    "downlink_interfaces": ["Ethernet8"],
                },
                {
                    "types": ["l2leaf"],
                    "platforms": ["default"],
                    "uplink_interfaces": ["Ethernet1-2"],
                },
            ],
        }

        # Generate SPINES group_vars
        spines_vars: dict[str, Any] = {
            "type": "spine",
            "spine": {
                "defaults": {
                    "platform": "vEOS-lab",
                    "loopback_ipv4_pool": "10.255.0.0/27",
                    "bgp_as": 65100,
                },
                "nodes": [],
            },
        }

        for idx, hostname in enumerate(sorted(spines.keys()), 1):
            node: dict[str, Any] = {"name": hostname, "id": idx}
            if hostname in ip_lookup:
                mgmt_ip = ip_lookup[hostname].get("Management1") or ip_lookup[hostname].get("Management0")
                if mgmt_ip:
                    node["mgmt_ip"] = mgmt_ip
            spines_vars["spine"]["nodes"].append(node)

        # Generate L3_LEAFS group_vars
        l3leafs_vars: dict[str, Any] = {
            "type": "l3leaf",
            "l3leaf": {
                "defaults": {
                    "platform": "vEOS-lab",
                    "loopback_ipv4_pool": "10.255.0.0/27",
                    "loopback_ipv4_offset": len(spines),  # Offset to avoid spine IPs
                    "vtep_loopback_ipv4_pool": "10.255.1.0/27",
                    "uplink_switches": sorted(spines.keys()),
                    "uplink_ipv4_pool": "10.255.255.0/26",
                    "mlag_peer_ipv4_pool": "10.255.1.64/27",
                    "mlag_peer_l3_ipv4_pool": "10.255.1.96/27",
                    "virtual_router_mac_address": "00:1c:73:00:00:99",
                    "spanning_tree_priority": 4096,
                    "spanning_tree_mode": "mstp",
                },
                "node_groups": [],
            },
        }

        # Group l3leafs by MLAG pairs
        processed_leafs: set[str] = set()
        l3leaf_node_groups: list[dict[str, Any]] = []
        bgp_as_counter = 65101  # Start BGP AS for leafs

        for hostname in sorted(l3leafs.keys()):
            if hostname in processed_leafs:
                continue

            mlag_info = mlag_peers.get(hostname, {})
            peer = mlag_info.get("mlag_peer")

            if peer and peer in l3leafs:
                # MLAG pair - create node_group with bgp_as
                group_name = f"DC1_L3_LEAF{(bgp_as_counter - 65100)}"
                node_group: dict[str, Any] = {
                    "group": group_name,
                    "bgp_as": bgp_as_counter,
                    "nodes": [],
                }
                bgp_as_counter += 1

                for idx, leaf_name in enumerate([hostname, peer], 1):
                    node: dict[str, Any] = {"name": leaf_name, "id": idx}

                    # Add management IP
                    if leaf_name in ip_lookup:
                        mgmt_ip = ip_lookup[leaf_name].get("Management1") or ip_lookup[leaf_name].get("Management0")
                        if mgmt_ip:
                            node["mgmt_ip"] = mgmt_ip

                    node_group["nodes"].append(node)
                    processed_leafs.add(leaf_name)

                l3leaf_node_groups.append(node_group)
            else:
                # Standalone L3 leaf (shouldn't happen in typical L3LS, but handle it)
                node: dict[str, Any] = {"name": hostname, "id": 1}
                if hostname in ip_lookup:
                    mgmt_ip = ip_lookup[hostname].get("Management1") or ip_lookup[hostname].get("Management0")
                    if mgmt_ip:
                        node["mgmt_ip"] = mgmt_ip

                l3leaf_node_groups.append(
                    {
                        "group": hostname.replace("-", "_").upper(),
                        "bgp_as": bgp_as_counter,
                        "nodes": [node],
                    }
                )
                bgp_as_counter += 1
                processed_leafs.add(hostname)

        l3leafs_vars["l3leaf"]["node_groups"] = l3leaf_node_groups

        # Generate L2_LEAFS group_vars (if any L2 leafs exist)
        l2leafs_vars: dict[str, Any] = {}
        if l2leafs:
            l2leafs_vars = {
                "type": "l2leaf",
                "l2leaf": {
                    "defaults": {
                        "platform": "vEOS-lab",
                        "spanning_tree_mode": "mstp",
                    },
                    "node_groups": [],
                },
            }

            # Group L2 leafs by their uplink switches
            l2leaf_groups: dict[str, list[str]] = {}
            for hostname in l2leafs:
                uplink_info = uplinks.get(hostname, {})
                uplink_switches = tuple(sorted(uplink_info.get("uplink_switches", [])))
                group_key = "_".join(uplink_switches) if uplink_switches else "STANDALONE"
                if group_key not in l2leaf_groups:
                    l2leaf_groups[group_key] = []
                l2leaf_groups[group_key].append(hostname)

            group_counter = 1
            for uplink_key, leaf_names in l2leaf_groups.items():
                uplink_switches = uplink_key.split("_") if uplink_key != "STANDALONE" else []
                node_group: dict[str, Any] = {
                    "group": f"DC1_L2_LEAF{group_counter}",
                    "uplink_switches": uplink_switches or None,
                    "nodes": [],
                }
                # Remove None values
                node_group = {k: v for k, v in node_group.items() if v is not None}

                for idx, leaf_name in enumerate(sorted(leaf_names), 1):
                    node: dict[str, Any] = {"name": leaf_name, "id": idx}
                    if leaf_name in ip_lookup:
                        mgmt_ip = ip_lookup[leaf_name].get("Management1") or ip_lookup[leaf_name].get("Management0")
                        if mgmt_ip:
                            node["mgmt_ip"] = mgmt_ip
                    node_group["nodes"].append(node)

                l2leafs_vars["l2leaf"]["node_groups"].append(node_group)
                group_counter += 1

        # Generate NETWORK_SERVICES group_vars
        network_services_vars: dict[str, Any] = {"tenants": []}

        # Group VLANs by VRF prefix in name (e.g., VRF10_VLAN11 -> VRF10)
        vrf_vlans: dict[str, list[dict[str, Any]]] = {}
        l2_vlans: list[dict[str, Any]] = []

        for vlan in vlans:
            vlan_name = vlan.get("name", "")
            vlan_id = vlan.get("id")

            # Skip MLAG-specific VLANs (internal use)
            if vlan_name.startswith("MLAG"):
                continue

            # Check if VLAN belongs to a VRF
            vrf_match = None
            for vrf in vrfs:
                vrf_name = vrf.get("name", "")
                if vrf_name and vlan_name.startswith(f"{vrf_name}_"):
                    vrf_match = vrf_name
                    break

            if vrf_match:
                if vrf_match not in vrf_vlans:
                    vrf_vlans[vrf_match] = []
                vrf_vlans[vrf_match].append({"id": vlan_id, "name": vlan_name})
            else:
                l2_vlans.append({"id": vlan_id, "name": vlan_name})

        # Build tenants structure
        if vrf_vlans or l2_vlans:
            tenant: dict[str, Any] = {
                "name": "TENANT1",
                "mac_vrf_vni_base": 10000,
                "vrfs": [],
            }

            # Add VRFs with their SVIs
            vrf_vni_counter = 10
            for vrf_name in sorted(vrf_vlans.keys()):
                vlan_list = vrf_vlans[vrf_name]
                vrf_entry: dict[str, Any] = {
                    "name": vrf_name,
                    "vrf_vni": vrf_vni_counter,
                    "svis": [],
                }

                for vlan in sorted(vlan_list, key=lambda x: x.get("id", 0)):
                    svi: dict[str, Any] = {
                        "id": vlan["id"],
                        "name": vlan["name"],
                        "enabled": True,
                        # Generate placeholder IP - would need IP data from NetBox
                        "ip_address_virtual": f"10.{vrf_vni_counter}.{vlan['id']}.1/24",
                    }
                    vrf_entry["svis"].append(svi)

                tenant["vrfs"].append(vrf_entry)
                vrf_vni_counter += 1

            # Add L2 VLANs
            if l2_vlans:
                tenant["l2vlans"] = [{"id": v["id"], "name": v["name"]} for v in sorted(l2_vlans, key=lambda x: x.get("id", 0))]

            network_services_vars["tenants"].append(tenant)

        # Return organized structure
        return {
            "FABRIC": fabric_vars,
            "SPINES": spines_vars,
            "L3_LEAFS": l3leafs_vars,
            "L2_LEAFS": l2leafs_vars if l2leafs else None,
            "NETWORK_SERVICES": network_services_vars,
        }
