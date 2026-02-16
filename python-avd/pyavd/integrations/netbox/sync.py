# Copyright (c) 2023-2026 Arista Networks, Inc.
# Use of this source code is governed by the Apache License 2.0
# that can be found in the LICENSE file.
# pylint: disable=too-many-lines
"""
AVD to NetBox Synchronization Logic.

Provides synchronization from AVD structured configuration data to NetBox.
This module syncs devices, interfaces, VLANs, VRFs, cables, IP addresses,
prefixes, ASNs, port-channels (LAGs), and interface VLAN/VRF associations.
"""

from __future__ import annotations

import ipaddress
import logging
import re
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
        site_mapping: Dict mapping hostname prefix to site name (e.g., {"dc1": "DC1_Site", "dc2": "DC2_Site"})
        create_prerequisites: If True, create missing sites/roles/types in NetBox
    """

    def __init__(
        self,
        client: NetBoxClient,
        mapping: AVDNetBoxMapping | None = None,
        *,
        dry_run: bool = False,
        site_name: str | None = None,
        site_mapping: dict[str, str] | None = None,
        create_prerequisites: bool = True,
    ) -> None:
        self.client = client
        self.mapping = mapping or AVDNetBoxMapping()
        self.dry_run = dry_run
        self.site_name = site_name
        self.site_mapping = site_mapping or {}
        self.create_prerequisites = create_prerequisites
        self._cache: dict[str, dict[str, Any]] = {}
        self._prerequisites_created = False
        self._site_cache: dict[str, dict[str, Any]] = {}  # Cache for site lookups by name

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

    def _get_or_create_site(self, site_name: str) -> dict[str, Any] | None:
        """
        Get or create a site by name.

        Args:
            site_name: Name of the site to get or create

        Returns:
            Site dict with 'id' field, or None if dry_run
        """
        if self.dry_run:
            return None

        # Check cache first
        if site_name in self._site_cache:
            return self._site_cache[site_name]

        endpoints = self.mapping.get_netbox_endpoints()

        # Try to find existing site
        site = self._find_netbox_object(endpoints["sites"], name=site_name)
        if not site and self.create_prerequisites:
            LOGGER.info("Creating site: %s", site_name)
            site = self.client.post(
                endpoints["sites"],
                {
                    "name": site_name,
                    "slug": slugify(site_name),
                    "status": "active",
                },
            )

        if site:
            self._site_cache[site_name] = site

        return site

    def _get_site_for_hostname(self, hostname: str) -> dict[str, Any] | None:
        """
        Get the appropriate site for a hostname based on site_mapping or default site_name.

        Args:
            hostname: Device hostname

        Returns:
            Site dict with 'id' field, or None if no site mapping found
        """
        # Check site_mapping for prefix match
        if self.site_mapping:
            hostname_lower = hostname.lower()
            for prefix, site_name in self.site_mapping.items():
                if hostname_lower.startswith(prefix.lower()):
                    return self._get_or_create_site(site_name)

        # Fall back to default site_name
        if self.site_name:
            return self._get_or_create_site(self.site_name)

        return None

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
            # Get site for this hostname (using site_mapping or default site_name)
            site = self._get_site_for_hostname(hostname)

            if existing:
                # Update existing device - also update site if site_mapping is used
                if site and self.site_mapping:
                    netbox_data["site"] = site["id"]
                self.client.patch(f"{endpoints['devices']}{existing['id']}/", netbox_data)
                result.updated += 1
                LOGGER.debug("Updated device: %s", hostname)
            else:
                # Add required fields for new device
                if site:
                    netbox_data["site"] = site["id"]
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

        # Handle interface mode from switchport config (new AVD format)
        # The mapping looks for "mode" at top level, but AVD uses "switchport.mode"
        switchport = intf_data.get("switchport", {})
        if switchport.get("enabled", True) and switchport.get("mode"):
            netbox_data["mode"] = apply_transform("map_interface_mode", switchport["mode"])

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
                # Extract VRF ID from the interface (was set during interface creation/update)
                # This allows duplicate IPs across different VRFs in NetBox
                vrf_data = intf_for_ip.get("vrf")
                vrf_id = vrf_data.get("id") if isinstance(vrf_data, dict) else vrf_data

                # Sync IP address if present (regular IP)
                if ip_addr := intf_data.get("ip_address"):
                    self._sync_interface_ip(ip_addr, intf_for_ip, endpoints, vrf_id=vrf_id)

                # Sync virtual IP address if present (anycast gateway IPs on SVIs)
                if virtual_ip := intf_data.get("ip_address_virtual"):
                    self._sync_interface_ip(virtual_ip, intf_for_ip, endpoints, role="anycast", vrf_id=vrf_id)

        except Exception as e:
            result.errors.append(f"Failed to sync interface {intf_name}: {e}")

        return result

    def _sync_interface_ip(
        self,
        ip_address: str,
        interface: dict[str, Any],
        endpoints: dict[str, str],
        role: str | None = None,
        vrf_id: int | None = None,
    ) -> dict[str, Any] | None:
        """
        Sync IP address for an interface.

        Always looks for an existing IP by address AND assigned interface.
        This handles several network design patterns where the same IP exists multiple times:
        - Anycast IPs (same IP on multiple devices for gateway redundancy)
        - Overlapping IP space across VRFs (same IP on different VLAN interfaces in different VRFs)
        - MLAG peer-link VLANs (same subnet used for L3 peering in multiple VRFs)

        Args:
            ip_address: IP address in CIDR notation
            interface: NetBox interface dict (must have 'id')
            endpoints: NetBox API endpoints
            role: Optional IP role (e.g., 'anycast' for virtual IPs)
            vrf_id: Optional VRF ID for the IP (allows duplicate IPs in different VRFs)

        Returns:
            Created/updated IP address object dict, or None if failed
        """
        if self.dry_run:
            return None

        intf_id = interface.get("id")
        if not intf_id:
            return None

        ip_data: dict[str, Any] = {
            "address": ip_address,
            "assigned_object_type": "dcim.interface",
            "assigned_object_id": intf_id,
        }

        # Add role if specified (e.g., "anycast" for virtual IPs)
        if role:
            ip_data["role"] = role

        # Add VRF if specified (allows duplicate IPs across different VRFs)
        if vrf_id:
            ip_data["vrf"] = vrf_id

        # Always look for existing IP assigned to THIS specific interface
        # This handles: anycast IPs, overlapping VRF IPs, MLAG peering IPs, etc.
        existing_ip = self._find_ip_for_interface(endpoints["ip_addresses"], ip_address, intf_id)

        try:
            if existing_ip:
                self.client.patch(f"{endpoints['ip_addresses']}{existing_ip['id']}/", ip_data)
                return existing_ip
            return self.client.post(endpoints["ip_addresses"], ip_data)
        except Exception as e:
            LOGGER.warning("Failed to sync IP %s: %s", ip_address, e)
            return None

    def _find_ip_for_interface(self, ip_endpoint: str, address: str, interface_id: int) -> dict[str, Any] | None:
        """
        Find an IP address assigned to a specific interface.

        Args:
            ip_endpoint: NetBox IP addresses API endpoint
            address: IP address to search for
            interface_id: Interface ID to match

        Returns:
            IP address dict if found, None otherwise
        """
        # Get all IPs with this address
        for ip in self.client.get_all(ip_endpoint, params={"address": address}):
            assigned_obj = ip.get("assigned_object")
            if assigned_obj and assigned_obj.get("id") == interface_id:
                return ip
        return None

    def sync_vlans(self, avd_structured_config: dict[str, Any]) -> SyncResult:
        """Sync VLANs from AVD structured config to NetBox."""
        result = SyncResult()
        vlans = avd_structured_config.get("vlans", [])
        endpoints = self.mapping.get_netbox_endpoints()

        # Get site ID for site-scoped VLAN lookup
        site_id = None
        if self.site_name:
            site = self._find_netbox_object(endpoints["sites"], name=self.site_name)
            if site:
                site_id = site["id"]

        for vlan in vlans:
            vlan_id = vlan.get("id")
            if not vlan_id:
                continue

            netbox_data = self._transform_avd_to_netbox(vlan, self.mapping.vlan_mappings)

            if site_id:
                netbox_data["site"] = site_id

            # Look for existing VLAN by VID AND site (or global if no site)
            if site_id:
                existing = self._find_netbox_object(endpoints["vlans"], vid=vlan_id, site_id=site_id)
            else:
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

        # Invalidate VLAN cache since we may have created/updated VLANs
        self._cache.pop("vlans_by_vid_site", None)

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

    def sync_prefix(self, prefix: str, vrf_name: str | None = None, description: str = "") -> SyncResult:
        """
        Sync a prefix (IP subnet) to NetBox.

        Args:
            prefix: IPv4/IPv6 network in CIDR notation (e.g., "10.0.0.0/24")
            vrf_name: Optional VRF name to assign to the prefix
            description: Optional description for the prefix

        Returns:
            SyncResult with operation counts
        """
        result = SyncResult()
        endpoints = self.mapping.get_netbox_endpoints()

        # Validate prefix format
        try:
            ipaddress.ip_network(prefix, strict=False)
        except ValueError as e:
            result.errors.append(f"Invalid prefix format {prefix}: {e}")
            return result

        # Look up VRF ID if provided
        vrf_id = None
        if vrf_name:
            vrf = self._find_netbox_object(endpoints["vrfs"], name=vrf_name)
            if vrf:
                vrf_id = vrf["id"]

        # Check if prefix already exists
        search_params: dict[str, Any] = {"prefix": prefix}
        if vrf_id:
            search_params["vrf_id"] = vrf_id
        existing = self._find_netbox_object(endpoints["prefixes"], **search_params)

        if self.dry_run:
            LOGGER.info("[DRY RUN] Would %s prefix: %s", "update" if existing else "create", prefix)
            result.skipped += 1
            return result

        prefix_data: dict[str, Any] = {
            "prefix": prefix,
            "status": "active",
        }
        if vrf_id:
            prefix_data["vrf"] = vrf_id
        if description:
            prefix_data["description"] = description

        try:
            if existing:
                self.client.patch(f"{endpoints['prefixes']}{existing['id']}/", prefix_data)
                result.updated += 1
            else:
                self.client.post(endpoints["prefixes"], prefix_data)
                result.created += 1
        except Exception as e:
            result.errors.append(f"Failed to sync prefix {prefix}: {e}")

        return result

    def sync_prefixes_from_config(self, avd_structured_config: dict[str, Any]) -> SyncResult:
        """
        Extract and sync all prefixes from AVD structured config.

        Extracts prefixes from:
        - Loopback interfaces (as /32 or /128 host routes, plus network prefixes)
        - VLAN interfaces (SVI subnets)
        - Management interfaces
        - P2P links on ethernet interfaces

        Args:
            avd_structured_config: AVD structured configuration for a device

        Returns:
            SyncResult with operation counts
        """
        result = SyncResult()
        synced_prefixes: set[tuple[str, str | None]] = set()  # (prefix, vrf_name) to avoid duplicates

        # Extract from loopback interfaces
        for loop in avd_structured_config.get("loopback_interfaces", []):
            if ip_addr := loop.get("ip_address"):
                # Convert host IP to network prefix (e.g., 10.0.0.1/32 stays as /32 for loopbacks)
                try:
                    network = ipaddress.ip_network(ip_addr, strict=False)
                    prefix_str = str(network)
                    vrf_name = loop.get("vrf")
                    key = (prefix_str, vrf_name)
                    if key not in synced_prefixes:
                        synced_prefixes.add(key)
                        result = result + self.sync_prefix(prefix_str, vrf_name, loop.get("description", ""))
                except ValueError:
                    continue

        # Extract from VLAN interfaces (SVIs)
        for vlan_intf in avd_structured_config.get("vlan_interfaces", []):
            vrf_name = vlan_intf.get("vrf")
            description = vlan_intf.get("description", "")

            # Regular IP address
            if ip_addr := vlan_intf.get("ip_address"):
                try:
                    network = ipaddress.ip_network(ip_addr, strict=False)
                    prefix_str = str(network)
                    key = (prefix_str, vrf_name)
                    if key not in synced_prefixes:
                        synced_prefixes.add(key)
                        result = result + self.sync_prefix(prefix_str, vrf_name, description)
                except ValueError:
                    continue

            # Virtual IP (anycast gateway)
            if virtual_ip := vlan_intf.get("ip_address_virtual"):
                try:
                    network = ipaddress.ip_network(virtual_ip, strict=False)
                    prefix_str = str(network)
                    key = (prefix_str, vrf_name)
                    if key not in synced_prefixes:
                        synced_prefixes.add(key)
                        result = result + self.sync_prefix(prefix_str, vrf_name, f"SVI {vlan_intf.get('name', '')} anycast")
                except ValueError:
                    continue

        # Extract from management interfaces
        for mgmt in avd_structured_config.get("management_interfaces", []):
            if ip_addr := mgmt.get("ip_address"):
                try:
                    network = ipaddress.ip_network(ip_addr, strict=False)
                    prefix_str = str(network)
                    vrf_name = mgmt.get("vrf")
                    key = (prefix_str, vrf_name)
                    if key not in synced_prefixes:
                        synced_prefixes.add(key)
                        result = result + self.sync_prefix(prefix_str, vrf_name, "Management Network")
                except ValueError:
                    continue

        # Extract from P2P ethernet interface links
        for eth in avd_structured_config.get("ethernet_interfaces", []):
            if ip_addr := eth.get("ip_address"):
                try:
                    network = ipaddress.ip_network(ip_addr, strict=False)
                    # Only sync /30 or /31 P2P links as prefixes
                    if network.prefixlen >= 30 or (network.version == 6 and network.prefixlen >= 126):
                        prefix_str = str(network)
                        vrf_name = eth.get("vrf")
                        key = (prefix_str, vrf_name)
                        if key not in synced_prefixes:
                            synced_prefixes.add(key)
                            result = result + self.sync_prefix(prefix_str, vrf_name, eth.get("description", "P2P Link"))
                except ValueError:
                    continue

        return result

    def sync_asn(self, asn: int | str) -> SyncResult:
        """
        Sync an ASN (Autonomous System Number) to NetBox.

        Args:
            asn: BGP AS number (supports asdot notation like "65001.1")

        Returns:
            SyncResult with operation counts
        """
        result = SyncResult()
        endpoints = self.mapping.get_netbox_endpoints()

        # Convert asdot notation to integer if needed
        asn_int = self._parse_asn(asn)
        if asn_int is None:
            result.errors.append(f"Invalid ASN format: {asn}")
            return result

        # Check if ASN already exists
        existing = self._find_netbox_object(endpoints["asns"], asn=asn_int)

        if self.dry_run:
            LOGGER.info("[DRY RUN] Would %s ASN: %s", "update" if existing else "create", asn_int)
            result.skipped += 1
            return result

        # Ensure RIR exists (required by NetBox)
        rir = self._ensure_rir()
        if not rir:
            result.errors.append("Failed to create/find RIR for ASN")
            return result

        asn_data = {
            "asn": asn_int,
            "rir": rir["id"],
        }

        try:
            if existing:
                self.client.patch(f"{endpoints['asns']}{existing['id']}/", asn_data)
                result.updated += 1
            else:
                self.client.post(endpoints["asns"], asn_data)
                result.created += 1
            LOGGER.debug("Synced ASN %s", asn_int)
        except Exception as e:
            result.errors.append(f"Failed to sync ASN {asn_int}: {e}")

        return result

    def _parse_asn(self, asn: int | str) -> int | None:
        """
        Parse ASN from various formats to integer.

        Supports:
        - Plain integer: 65001
        - String integer: "65001"
        - Asdot notation: "65001.100" (converts to 4260032612)
        """
        if isinstance(asn, int):
            return asn
        if isinstance(asn, str):
            if "." in asn:
                # Asdot notation: high.low
                try:
                    parts = asn.split(".")
                    if len(parts) == 2:
                        high = int(parts[0])
                        low = int(parts[1])
                        return (high << 16) + low
                except ValueError:
                    return None
            else:
                try:
                    return int(asn)
                except ValueError:
                    return None
        return None

    def _ensure_rir(self) -> dict[str, Any] | None:
        """Ensure a default RIR exists in NetBox for ASN assignments."""
        endpoints = self.mapping.get_netbox_endpoints()
        rir = self._find_netbox_object(endpoints["rirs"], slug="private")
        if rir:
            return rir

        # Create a private/internal RIR
        try:
            return self.client.post(
                endpoints["rirs"],
                {
                    "name": "Private",
                    "slug": "private",
                    "is_private": True,
                },
            )
        except Exception as e:
            LOGGER.warning("Failed to create RIR: %s", e)
            return None

    def sync_asns_from_config(self, avd_structured_config: dict[str, Any]) -> SyncResult:
        """
        Extract and sync ASNs from AVD structured config.

        Extracts ASNs from:
        - router_bgp.as (local AS number)
        - BGP neighbors (remote AS numbers)

        Args:
            avd_structured_config: AVD structured configuration for a device

        Returns:
            SyncResult with operation counts
        """
        result = SyncResult()
        synced_asns: set[int] = set()

        # Extract from router_bgp.as
        router_bgp = avd_structured_config.get("router_bgp", {})
        if bgp_as := router_bgp.get("as"):
            asn_int = self._parse_asn(bgp_as)
            if asn_int and asn_int not in synced_asns:
                synced_asns.add(asn_int)
                result = result + self.sync_asn(bgp_as)

        # Extract from BGP neighbors
        for neighbor in router_bgp.get("neighbors", []):
            if remote_as := neighbor.get("remote_as"):
                asn_int = self._parse_asn(remote_as)
                if asn_int and asn_int not in synced_asns:
                    synced_asns.add(asn_int)
                    result = result + self.sync_asn(remote_as)

        return result

    def sync_port_channels(self, avd_structured_config: dict[str, Any]) -> SyncResult:
        """
        Sync port-channel interfaces as LAG type and update member interface LAG assignments.

        This method:
        1. Creates/updates port-channel interfaces with type='lag' in NetBox
        2. Updates member ethernet interfaces with the 'lag' field pointing to the port-channel

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

        # Find device in NetBox
        device = self._find_netbox_object(endpoints["devices"], name=hostname)
        if not device:
            LOGGER.debug("Device %s not found in NetBox for port-channel sync", hostname)
            return result

        device_id = device["id"]

        # Build a map of ethernet interfaces to their channel groups
        eth_to_channel: dict[str, int] = {}
        for eth in avd_structured_config.get("ethernet_interfaces", []):
            if channel_group := eth.get("channel_group"):
                channel_id = channel_group.get("id")
                eth_name = eth.get("name")
                if channel_id and eth_name:
                    eth_to_channel[eth_name] = channel_id

        # Process port-channel interfaces
        for pc in avd_structured_config.get("port_channel_interfaces", []):
            pc_name = pc.get("name")
            if not pc_name:
                continue

            # Extract port-channel ID from name (e.g., "Port-Channel5" -> 5)
            pc_id = self._extract_port_channel_id(pc_name)
            if pc_id is None:
                continue

            # Find or create the port-channel interface in NetBox
            existing_pc = self._find_netbox_object(endpoints["interfaces"], device_id=device_id, name=pc_name)

            pc_data: dict[str, Any] = {
                "name": pc_name,
                "device": device_id,
                "type": "lag",  # LAG type for port-channels
                "description": pc.get("description", ""),
            }

            # Handle mode from switchport config
            switchport = pc.get("switchport", {})
            if switchport.get("mode"):
                mode = switchport["mode"]
                if mode == "trunk":
                    pc_data["mode"] = "tagged"
                elif mode == "access":
                    pc_data["mode"] = "access"

            if self.dry_run:
                result.skipped += 1
                continue

            try:
                if existing_pc:
                    self.client.patch(f"{endpoints['interfaces']}{existing_pc['id']}/", pc_data)
                    result.updated += 1
                    pc_intf_id = existing_pc["id"]
                else:
                    new_pc = self.client.post(endpoints["interfaces"], pc_data)
                    result.created += 1
                    pc_intf_id = new_pc["id"]

                # Update member interfaces with LAG assignment
                for eth_name, channel_id in eth_to_channel.items():
                    if channel_id == pc_id:
                        eth_intf = self._find_netbox_object(endpoints["interfaces"], device_id=device_id, name=eth_name)
                        if eth_intf:
                            self.client.patch(f"{endpoints['interfaces']}{eth_intf['id']}/", {"lag": pc_intf_id})
                            LOGGER.debug("Assigned %s to LAG %s", eth_name, pc_name)

            except Exception as e:
                result.errors.append(f"Failed to sync port-channel {pc_name}: {e}")

        return result

    def _extract_port_channel_id(self, name: str) -> int | None:
        """Extract port-channel ID from interface name (e.g., 'Port-Channel5' -> 5)."""
        match = re.match(r"[Pp]ort-?[Cc]hannel(\d+)", name)
        if match:
            return int(match.group(1))
        return None

    def sync_interface_vlan_associations(self, avd_structured_config: dict[str, Any]) -> SyncResult:
        """
        Sync VLAN associations (tagged_vlans, untagged_vlan) for interfaces.

        Updates ethernet and port-channel interfaces with their VLAN assignments:
        - trunk mode -> tagged_vlans (list of VLAN IDs)
        - access mode -> untagged_vlan (single VLAN ID)
        - trunk native_vlan -> untagged_vlan

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

        # Find device in NetBox
        device = self._find_netbox_object(endpoints["devices"], name=hostname)
        if not device:
            return result

        device_id = device["id"]

        # Get device's site ID to filter VLANs properly
        device_site_id = self._extract_site_id(device.get("site"))

        # Build site-filtered VLAN lookup cache (vid -> netbox_id)
        vlan_cache = self._get_site_vlan_cache(endpoints["vlans"], device_site_id)

        # Process ethernet interfaces
        for eth in avd_structured_config.get("ethernet_interfaces", []):
            intf_result = self._sync_interface_vlans(eth, device_id, endpoints, vlan_cache)
            result = result + intf_result

        # Process port-channel interfaces
        for pc in avd_structured_config.get("port_channel_interfaces", []):
            intf_result = self._sync_interface_vlans(pc, device_id, endpoints, vlan_cache)
            result = result + intf_result

        return result

    def _extract_site_id(self, site_data: Any) -> int | None:
        """Extract site ID from site data which can be a dict or an int."""
        if not site_data:
            return None
        if isinstance(site_data, dict):
            return site_data.get("id")
        return site_data

    def _get_site_vlan_cache(self, vlans_endpoint: str, site_id: int | None) -> dict[int, Any]:
        """
        Build a VLAN cache filtered by site.

        Returns VLANs that belong to the specified site or are global (no site).

        Args:
            vlans_endpoint: NetBox VLAN API endpoint
            site_id: Site ID to filter by, or None for all VLANs

        Returns:
            Dict mapping VLAN VID to VLAN object
        """
        cache_key = f"vlans_by_vid_site_{site_id}"
        if cache_key in self._cache:
            return self._cache[cache_key]  # pyright: ignore[reportReturnType]

        vlan_cache: dict[int, Any] = {}
        for vlan in self.client.get_all(vlans_endpoint):
            vid = vlan.get("vid")
            if not vid:
                continue

            # Get VLAN's site ID
            vlan_site_id = self._extract_site_id(vlan.get("site"))

            # Include VLAN if it belongs to the device's site or is global (no site)
            # Prefer site-specific VLAN over global if there's a conflict
            if (site_id is None or vlan_site_id is None or vlan_site_id == site_id) and (vid not in vlan_cache or vlan_site_id == site_id):
                vlan_cache[vid] = vlan

        self._cache[cache_key] = vlan_cache  # pyright: ignore[reportArgumentType]
        return vlan_cache

    def _sync_interface_vlans(
        self,
        intf_data: dict[str, Any],
        device_id: int,
        endpoints: dict[str, str],
        vlan_cache: dict[Any, Any],
    ) -> SyncResult:
        """Sync VLAN associations for a single interface."""
        result = SyncResult()
        intf_name = intf_data.get("name")
        if not intf_name:
            return result

        # Find interface in NetBox
        intf = self._find_netbox_object(endpoints["interfaces"], device_id=device_id, name=intf_name)
        if not intf:
            return result

        update_data: dict[str, Any] = {}

        # Get switchport config (new AVD format)
        switchport = intf_data.get("switchport", {})
        mode = switchport.get("mode") or intf_data.get("mode")
        trunk_config = switchport.get("trunk", {})

        if mode == "trunk":
            # Handle trunk mode
            allowed_vlans_str = trunk_config.get("allowed_vlan") or intf_data.get("vlans")
            if allowed_vlans_str:
                vlan_ids = self._parse_vlan_list(str(allowed_vlans_str))
                tagged_vlan_ids = [vlan_cache[vid]["id"] for vid in vlan_ids if vid in vlan_cache]
                if tagged_vlan_ids:
                    update_data["tagged_vlans"] = tagged_vlan_ids

            # Handle native VLAN
            native_vlan = trunk_config.get("native_vlan") or intf_data.get("native_vlan")
            if native_vlan and int(native_vlan) in vlan_cache:
                update_data["untagged_vlan"] = vlan_cache[int(native_vlan)]["id"]

        elif mode == "access":
            # Handle access mode
            access_vlan = switchport.get("access_vlan") or intf_data.get("access_vlan")
            if access_vlan and int(access_vlan) in vlan_cache:
                update_data["untagged_vlan"] = vlan_cache[int(access_vlan)]["id"]

        if not update_data:
            return result

        if self.dry_run:
            result.skipped += 1
            return result

        try:
            self.client.patch(f"{endpoints['interfaces']}{intf['id']}/", update_data)
            result.updated += 1
        except Exception as e:
            result.errors.append(f"Failed to update VLAN associations for {intf_name}: {e}")

        return result

    def _parse_vlan_list(self, vlan_str: str) -> list[int]:
        """
        Parse VLAN list string into list of VLAN IDs.

        Handles formats like:
        - "10" -> [10]
        - "10,20,30" -> [10, 20, 30]
        - "10-15" -> [10, 11, 12, 13, 14, 15]
        - "10-12,20,30-32" -> [10, 11, 12, 20, 30, 31, 32]
        """
        vlan_ids: list[int] = []
        for raw_part in vlan_str.split(","):
            segment = raw_part.strip()
            if "-" in segment:
                try:
                    start, end = segment.split("-")
                    vlan_ids.extend(range(int(start), int(end) + 1))
                except ValueError:
                    continue
            else:
                try:
                    vlan_ids.append(int(segment))
                except ValueError:
                    continue
        return vlan_ids

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

        # First pass: sync VRFs and VLANs so they exist for interface assignments
        for config in avd_structured_configs.values():
            result = result + self.sync_vrfs(config)
            result = result + self.sync_vlans(config)

        # Second pass: sync devices and interfaces
        for hostname, config in avd_structured_configs.items():
            LOGGER.info("Syncing device %s to NetBox", hostname)
            node_type = node_types.get(hostname)

            # Sync device first
            result = result + self.sync_device(config, node_type)

            # Then sync interfaces (need VRFs/VLANs to exist already)
            result = result + self.sync_interfaces(config)

        # Third pass: sync port-channels and update interface LAG memberships
        for config in avd_structured_configs.values():
            result = result + self.sync_port_channels(config)

        # Fourth pass: sync interface VLAN associations (needs VLANs and interfaces to exist)
        for config in avd_structured_configs.values():
            result = result + self.sync_interface_vlan_associations(config)

        # Fifth pass: set primary IPs after interfaces and IPs exist
        for config in avd_structured_configs.values():
            result = result + self.sync_primary_ip(config)

        # Sixth pass: sync prefixes and ASNs
        for config in avd_structured_configs.values():
            result = result + self.sync_prefixes_from_config(config)
            result = result + self.sync_asns_from_config(config)

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
