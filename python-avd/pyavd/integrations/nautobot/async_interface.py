# Copyright (c) 2023-2026 Arista Networks, Inc.
# Use of this source code is governed by the Apache License 2.0
# that can be found in the LICENSE file.
"""Async interface sync methods for AVD to Nautobot synchronization."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

from .models import SyncResult
from .transforms import map_interface_mode, map_interface_type

if TYPE_CHECKING:
    from .client import AsyncNautobotClient
    from .models import AVDNautobotMapping

LOGGER = logging.getLogger(__name__)


class AsyncInterfaceMixin:
    """Mixin class providing async interface sync methods."""

    client: AsyncNautobotClient
    mapping: AVDNautobotMapping
    dry_run: bool
    _namespace_id: str | None
    _touched_objects: dict[str, set[str]]

    # These methods are provided by AsyncHelpersMixin
    async def _find_nautobot_object(self, endpoint: str, **kwargs: Any) -> dict[str, Any] | None: ...
    async def _apply_managed_tag(self, endpoint: str, obj_id: str) -> bool: ...
    async def _get_status_id(self, status_name: str, content_type: str) -> str | None: ...
    async def _ensure_namespace(self) -> str | None: ...
    async def _ensure_managed_tag(self) -> str | None: ...
    def _track_object(self, obj_type: str, obj_id: str) -> None: ...

    async def _sync_interfaces_async(
        self,
        avd_structured_config: dict[str, Any],
        endpoints: dict[str, str],
    ) -> SyncResult:
        """Sync all interfaces for a device using BULK operations."""
        result = SyncResult()
        hostname = avd_structured_config.get("hostname")
        if not hostname:
            return result

        device = await self._find_nautobot_object(endpoints["devices"], name=hostname)
        if not device:
            return result

        device_id = device["id"]

        # Get status ID for interfaces
        status_id = await self._get_status_id("Active", "dcim.interface")

        # Collect ALL interfaces from all types
        all_interfaces: list[dict[str, Any]] = []
        interface_types = [
            "ethernet_interfaces",
            "loopback_interfaces",
            "vlan_interfaces",
            "port_channel_interfaces",
            "management_interfaces",
        ]

        for intf_type in interface_types:
            interfaces = avd_structured_config.get(intf_type, [])
            all_interfaces.extend(interface for interface in interfaces if isinstance(interface, dict) and interface.get("name"))

        if not all_interfaces:
            return result

        # Get existing interfaces for this device (one API call)
        existing_interfaces: dict[str, dict[str, Any]] = {}
        async for intf in self.client.get_all(endpoints["interfaces"], params={"device_id": device_id}):
            existing_interfaces[intf["name"]] = intf

        # Separate interfaces into create vs update batches
        interfaces_to_create: list[dict[str, Any]] = []
        interfaces_to_update: list[dict[str, Any]] = []
        interface_config_map: dict[str, dict[str, Any]] = {}  # For IP sync later

        for interface in all_interfaces:
            intf_name = interface["name"]
            interface_config_map[intf_name] = interface

            # Build Nautobot interface data
            nautobot_data: dict[str, Any] = {
                "device": device_id,
                "name": intf_name,
                "type": map_interface_type(intf_name),
            }
            if status_id:
                nautobot_data["status"] = status_id
            if "description" in interface:
                nautobot_data["description"] = interface["description"]
            if "shutdown" in interface:
                nautobot_data["enabled"] = not interface["shutdown"]
            switchport = interface.get("switchport", {})
            if switchport.get("mode"):
                nautobot_data["mode"] = map_interface_mode(switchport["mode"])

            if intf_name in existing_interfaces:
                # Update existing - include ID
                nautobot_data["id"] = existing_interfaces[intf_name]["id"]
                interfaces_to_update.append(nautobot_data)
            else:
                interfaces_to_create.append(nautobot_data)

        if self.dry_run:
            result.skipped += len(interfaces_to_create) + len(interfaces_to_update)
            return result

        # Get managed tag ID to include in create payloads (avoids individual apply calls)
        tag_id = await self._ensure_managed_tag()

        # BULK CREATE interfaces (include tag in payload)
        if interfaces_to_create:
            try:
                # Add tag to each interface to create
                if tag_id:
                    for intf_data in interfaces_to_create:
                        intf_data["tags"] = [tag_id]

                created = await self.client.bulk_create(endpoints["interfaces"], interfaces_to_create)
                result.created += len(created)
                for intf in created:
                    existing_interfaces[intf["name"]] = intf
                    self._track_object("interfaces", intf["id"])
                LOGGER.debug("Bulk created %d interfaces for %s", len(created), hostname)
            except Exception as e:
                error_msg = f"Bulk create interfaces for {hostname} failed: {e}"
                result.errors.append(error_msg)
                LOGGER.warning(error_msg)

        # BULK UPDATE interfaces
        if interfaces_to_update:
            try:
                updated = await self.client.bulk_update(endpoints["interfaces"], interfaces_to_update)
                result.updated += len(updated)
                for intf in updated:
                    self._track_object("interfaces", intf["id"])
                LOGGER.debug("Bulk updated %d interfaces for %s", len(updated), hostname)
            except Exception as e:
                error_msg = f"Bulk update interfaces for {hostname} failed: {e}"
                result.errors.append(error_msg)
                LOGGER.warning(error_msg)

        # Sync IP addresses (still need individual calls for IP-to-interface assignments)
        for intf_name, intf_config in interface_config_map.items():
            if intf_name in existing_interfaces:
                intf_id = existing_interfaces[intf_name]["id"]
                await self._sync_interface_ips(intf_config, intf_id, endpoints)

        return result

    def _is_valid_ip_address(self, ip_str: str) -> bool:
        """Check if a string is a valid IP address (with or without CIDR notation)."""
        # IPv4 pattern: x.x.x.x or x.x.x.x/y
        ipv4_pattern = r"^(\d{1,3}\.){3}\d{1,3}(/\d{1,2})?$"
        # IPv6 pattern: simplified check for colons
        ipv6_pattern = r"^[0-9a-fA-F:]+(/\d{1,3})?$"

        if re.match(ipv4_pattern, ip_str):
            # Additional validation for IPv4 octet values
            parts = ip_str.split("/", maxsplit=1)[0].split(".")
            return all(0 <= int(p) <= 255 for p in parts)
        return bool(re.match(ipv6_pattern, ip_str) and ":" in ip_str)

    async def _sync_interface_ips(
        self,
        interface_data: dict[str, Any],
        intf_id: str,
        endpoints: dict[str, str],
    ) -> None:
        """Sync IP addresses for an interface."""
        # Virtual IP addresses (anycast) - can be a string or list
        ip_virtual = interface_data.get("ip_address_virtual")
        if ip_virtual:
            if isinstance(ip_virtual, str):
                # Single virtual IP as a string
                if self._is_valid_ip_address(ip_virtual):
                    await self._sync_ip_address(ip_virtual, intf_id, endpoints, role="anycast")
            elif isinstance(ip_virtual, list):
                # List of virtual IPs
                for ip_entry in ip_virtual:
                    if isinstance(ip_entry, str) and self._is_valid_ip_address(ip_entry):
                        await self._sync_ip_address(ip_entry, intf_id, endpoints, role="anycast")

        # Regular IP address
        ip_addr = interface_data.get("ip_address")
        if ip_addr and self._is_valid_ip_address(ip_addr):
            await self._sync_ip_address(ip_addr, intf_id, endpoints)

    async def _sync_ip_address(
        self,
        ip_address: str,
        intf_id: str,
        endpoints: dict[str, str],
        role: str | None = None,
    ) -> None:
        """
        Sync a single IP address to Nautobot.

        Nautobot uses a separate ip-address-to-interface endpoint for assignments.
        """
        # Get namespace
        namespace_id = await self._ensure_namespace()

        # Get status for IP address
        status_id = await self._get_status_id("Active", "ipam.ipaddress")

        # Get tag to include in payload (avoid separate tag application)
        tag_id = await self._ensure_managed_tag()

        # Build IP data
        ip_data: dict[str, Any] = {"address": ip_address}
        if namespace_id:
            ip_data["namespace"] = namespace_id
        if status_id:
            ip_data["status"] = status_id
        if role:
            ip_data["role"] = role
        if tag_id:
            ip_data["tags"] = [tag_id]

        # Check if IP already exists
        ip_by_address = await self._find_nautobot_object(endpoints["ip_addresses"], address=ip_address)

        if self.dry_run:
            return

        try:
            if ip_by_address:
                ip_id = ip_by_address["id"]
                # Update existing IP (keep existing tags)
                existing_tags = ip_by_address.get("tags", [])
                existing_tag_ids = [t["id"] if isinstance(t, dict) else t for t in existing_tags]
                if tag_id and tag_id not in existing_tag_ids:
                    existing_tag_ids.append(tag_id)
                    ip_data["tags"] = existing_tag_ids
                await self.client.patch(f"{endpoints['ip_addresses']}{ip_id}/", ip_data)
            else:
                # Create new IP (with tag included)
                new_ip = await self.client.post(endpoints["ip_addresses"], ip_data)
                ip_id = new_ip["id"]

            self._track_object("ip_addresses", ip_id)

            # Check if assignment already exists
            existing_assignment = None
            async for assignment in self.client.get_all(endpoints["ip_to_interface"], params={"ip_address": ip_id, "interface": intf_id}):
                existing_assignment = assignment
                break

            # Create ip-to-interface assignment if not exists
            if not existing_assignment:
                assignment_data = {
                    "ip_address": ip_id,
                    "interface": intf_id,
                }
                await self.client.post(endpoints["ip_to_interface"], assignment_data)
                LOGGER.debug("Created IP-to-interface assignment: %s -> %s", ip_address, intf_id)

        except Exception as e:
            LOGGER.debug("Failed to sync IP %s: %s", ip_address, e)
