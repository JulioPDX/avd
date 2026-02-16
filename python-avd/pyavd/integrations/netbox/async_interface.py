# Copyright (c) 2023-2026 Arista Networks, Inc.
# Use of this source code is governed by the Apache License 2.0
# that can be found in the LICENSE file.
"""Async interface sync methods for AVD to NetBox synchronization."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .sync import SyncResult
from .transforms import map_interface_mode, map_interface_type

if TYPE_CHECKING:
    from .client import AsyncNetBoxClient
    from .models import AVDNetBoxMapping

LOGGER = logging.getLogger(__name__)


class AsyncInterfaceMixin:
    """Mixin class providing async interface sync methods."""

    client: AsyncNetBoxClient
    mapping: AVDNetBoxMapping
    dry_run: bool
    _touched_objects: dict[str, set[int]]

    # These methods are provided by AsyncHelpersMixin
    async def _find_netbox_object(self, endpoint: str, **kwargs: Any) -> dict[str, Any] | None: ...
    async def _apply_managed_tag(self, endpoint: str, obj_id: int) -> bool: ...
    def _track_object(self, obj_type: str, obj_id: int) -> None: ...

    async def _sync_interfaces_async(
        self,
        avd_structured_config: dict[str, Any],
        endpoints: dict[str, str],
    ) -> SyncResult:
        """Sync all interfaces for a device."""
        result = SyncResult()
        hostname = avd_structured_config.get("hostname")
        if not hostname:
            return result

        device = await self._find_netbox_object(endpoints["devices"], name=hostname)
        if not device:
            return result

        device_id = device["id"]

        # Collect all interface types
        interface_types = [
            "ethernet_interfaces",
            "loopback_interfaces",
            "vlan_interfaces",
            "port_channel_interfaces",
            "management_interfaces",
        ]

        for intf_type in interface_types:
            interfaces = avd_structured_config.get(intf_type, [])
            for interface in interfaces:
                intf_result = await self._sync_single_interface(interface, device_id, endpoints)
                result = result + intf_result

        return result

    async def _sync_single_interface(
        self,
        interface_data: dict[str, Any],
        device_id: int,
        endpoints: dict[str, str],
    ) -> SyncResult:
        """Sync a single interface to NetBox."""
        result = SyncResult()
        intf_name = interface_data.get("name")
        if not intf_name:
            return result

        # Build interface data
        netbox_data: dict[str, Any] = {
            "device": device_id,
            "name": intf_name,
            "type": map_interface_type(intf_name),
        }

        # Add description if present
        if "description" in interface_data:
            netbox_data["description"] = interface_data["description"]

        # Add enabled status (inverse of shutdown)
        if "shutdown" in interface_data:
            netbox_data["enabled"] = not interface_data["shutdown"]

        # Add mode from switchport config
        switchport = interface_data.get("switchport", {})
        if switchport.get("mode"):
            netbox_data["mode"] = map_interface_mode(switchport["mode"])

        # Check for existing interface
        existing = await self._find_netbox_object(
            endpoints["interfaces"],
            device_id=device_id,
            name=intf_name,
        )

        if self.dry_run:
            result.skipped += 1
            return result

        try:
            if existing:
                await self.client.patch(f"{endpoints['interfaces']}{existing['id']}/", netbox_data)
                result.updated += 1
                intf_id = existing["id"]
            else:
                new_intf = await self.client.post(endpoints["interfaces"], netbox_data)
                result.created += 1
                intf_id = new_intf["id"]

            self._track_object("interfaces", intf_id)
            await self._apply_managed_tag(endpoints["interfaces"], intf_id)

            # Sync IP addresses for this interface
            await self._sync_interface_ips(interface_data, intf_id, endpoints)
        except Exception as e:
            error_msg = f"Failed to sync interface {intf_name}: {e}"
            result.errors.append(error_msg)
            LOGGER.warning(error_msg)

        return result

    async def _sync_interface_ips(
        self,
        interface_data: dict[str, Any],
        intf_id: int,
        endpoints: dict[str, str],
    ) -> None:
        """Sync IP addresses for an interface."""
        # Virtual IP addresses (anycast)
        for ip_entry in interface_data.get("ip_address_virtual", []):
            if isinstance(ip_entry, str):
                await self._sync_ip_address(ip_entry, intf_id, endpoints, role="anycast")

        # Regular IP address
        ip_addr = interface_data.get("ip_address")
        if ip_addr:
            await self._sync_ip_address(ip_addr, intf_id, endpoints)

    async def _sync_ip_address(
        self,
        ip_address: str,
        intf_id: int,
        endpoints: dict[str, str],
        role: str | None = None,
    ) -> None:
        """Sync a single IP address."""
        ip_data: dict[str, Any] = {
            "address": ip_address,
            "assigned_object_type": "dcim.interface",
            "assigned_object_id": intf_id,
        }
        if role:
            ip_data["role"] = role

        # Find existing IP for this interface
        existing = None
        async for ip in self.client.get_all(endpoints["ip_addresses"], params={"address": ip_address}):
            assigned_obj = ip.get("assigned_object")
            if assigned_obj and assigned_obj.get("id") == intf_id:
                existing = ip
                break

        if self.dry_run:
            return

        try:
            if existing:
                await self.client.patch(f"{endpoints['ip_addresses']}{existing['id']}/", ip_data)
                ip_id = existing["id"]
            else:
                new_ip = await self.client.post(endpoints["ip_addresses"], ip_data)
                ip_id = new_ip["id"]

            self._track_object("ip_addresses", ip_id)
            await self._apply_managed_tag(endpoints["ip_addresses"], ip_id)
        except Exception as e:
            LOGGER.debug("Failed to sync IP %s: %s", ip_address, e)
