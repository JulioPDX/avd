# Copyright (c) 2023-2026 Arista Networks, Inc.
# Use of this source code is governed by the Apache License 2.0
# that can be found in the LICENSE file.
"""
Async AVD to Nautobot Synchronization Logic.

Provides high-performance async synchronization from AVD structured configuration
data to Nautobot using concurrent requests.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import logging
from typing import TYPE_CHECKING, Any

from .async_device import AsyncDeviceMixin
from .async_helpers import AsyncHelpersMixin
from .async_interface import AsyncInterfaceMixin
from .models import AVDNautobotMapping, SyncResult

if TYPE_CHECKING:
    from .client import AsyncNautobotClient

LOGGER = logging.getLogger(__name__)


class AsyncAVDNautobotSync(AsyncHelpersMixin, AsyncDeviceMixin, AsyncInterfaceMixin):
    """
    Async synchronization of AVD structured configuration to Nautobot.

    Uses concurrent requests to significantly speed up synchronization.

    Args:
        client: Async Nautobot API client instance
        mapping: Field mapping configuration (uses defaults if not provided)
        dry_run: If True, don't make actual changes to Nautobot
        location_name: Default Nautobot location name for new objects
        location_mapping: Dict mapping hostname prefix to location name
        create_prerequisites: If True, create missing locations/roles/types in Nautobot
        managed_tag: Tag name to mark AVD-managed objects (default: "avd-managed")
        reconcile: If True, delete objects with managed_tag that weren't synced
        max_concurrent: Maximum concurrent operations per phase (default: 10)
    """

    DEFAULT_MANAGED_TAG = "avd-managed"

    def __init__(
        self,
        client: AsyncNautobotClient,
        mapping: AVDNautobotMapping | None = None,
        *,
        dry_run: bool = False,
        location_name: str | None = None,
        location_mapping: dict[str, str] | None = None,
        create_prerequisites: bool = True,
        managed_tag: str | None = None,
        reconcile: bool = False,
        max_concurrent: int = 10,
    ) -> None:
        self.client = client
        self.mapping = mapping or AVDNautobotMapping()
        self.dry_run = dry_run
        self.location_name = location_name
        self.location_mapping = location_mapping or {}
        self.create_prerequisites = create_prerequisites
        self.managed_tag = managed_tag or self.DEFAULT_MANAGED_TAG
        self.reconcile = reconcile
        self.max_concurrent = max_concurrent
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._cache: dict[str, dict[str, Any]] = {}
        self._prerequisites_created = False
        self._location_cache: dict[str, dict[str, Any]] = {}
        self._status_cache: dict[str, dict[str, Any]] = {}
        self._namespace_id: str | None = None
        self._managed_tag_id: str | None = None
        self._cache_lock = asyncio.Lock()
        self._prerequisite_lock = asyncio.Lock()

        # Track objects touched during sync for reconciliation (UUIDs as strings)
        self._touched_objects: dict[str, set[str]] = {
            "devices": set(),
            "interfaces": set(),
            "vlans": set(),
            "vrfs": set(),
            "ip_addresses": set(),
            "prefixes": set(),
            "cables": set(),
        }

    async def sync_vlans(
        self,
        avd_structured_config: dict[str, Any],
        endpoints: dict[str, str],
    ) -> SyncResult:
        """Sync VLANs from AVD config to Nautobot."""
        result = SyncResult()
        hostname = avd_structured_config.get("hostname", "")
        vlans = avd_structured_config.get("vlans", [])

        if not vlans:
            return result

        location = await self._get_location_for_hostname(hostname)
        location_id = location["id"] if location else None

        # Get status for Active VLAN
        status_id = await self._get_status_id("Active", "ipam.vlan")

        for vlan in vlans:
            vlan_id = vlan.get("id")
            vlan_name = vlan.get("name", f"VLAN{vlan_id}")
            if not vlan_id:
                continue

            vlan_data: dict[str, Any] = {"vid": vlan_id, "name": vlan_name}
            if status_id:
                vlan_data["status"] = status_id
            if location_id:
                vlan_data["location"] = location_id

            # Check for existing VLAN at this location
            params: dict[str, Any] = {"vid": vlan_id}
            if location_id:
                params["location"] = location_id
            existing = await self._find_nautobot_object(endpoints["vlans"], **params)

            if self.dry_run:
                result.skipped += 1
                continue

            try:
                if existing:
                    await self.client.patch(f"{endpoints['vlans']}{existing['id']}/", vlan_data)
                    result.updated += 1
                    obj_id = existing["id"]
                else:
                    new_vlan = await self.client.post(endpoints["vlans"], vlan_data)
                    result.created += 1
                    obj_id = new_vlan["id"]

                self._track_object("vlans", obj_id)
                await self._apply_managed_tag(endpoints["vlans"], obj_id)
            except Exception as e:
                error_msg = f"Failed to sync VLAN {vlan_id}: {e}"
                result.errors.append(error_msg)
                LOGGER.warning(error_msg)

        return result

    async def sync_vrfs(
        self,
        avd_structured_config: dict[str, Any],
        endpoints: dict[str, str],
    ) -> SyncResult:
        """Sync VRFs from AVD config to Nautobot."""
        result = SyncResult()
        vrfs = avd_structured_config.get("vrfs", [])

        # Get namespace
        namespace_id = await self._ensure_namespace()

        # Get status for Active VRF
        status_id = await self._get_status_id("Active", "ipam.vrf")

        for vrf in vrfs:
            vrf_name = vrf.get("name")
            if not vrf_name or vrf_name in ("default", "MGMT", "management"):
                continue

            vrf_data: dict[str, Any] = {"name": vrf_name}
            if namespace_id:
                vrf_data["namespace"] = namespace_id
            if status_id:
                vrf_data["status"] = status_id
            if "rd" in vrf:
                vrf_data["rd"] = vrf["rd"]

            existing = await self._find_nautobot_object(endpoints["vrfs"], name=vrf_name)

            if self.dry_run:
                result.skipped += 1
                continue

            try:
                if existing:
                    await self.client.patch(f"{endpoints['vrfs']}{existing['id']}/", vrf_data)
                    result.updated += 1
                    obj_id = existing["id"]
                else:
                    new_vrf = await self.client.post(endpoints["vrfs"], vrf_data)
                    result.created += 1
                    obj_id = new_vrf["id"]

                self._track_object("vrfs", obj_id)
                await self._apply_managed_tag(endpoints["vrfs"], obj_id)
            except Exception as e:
                error_msg = f"Failed to sync VRF {vrf_name}: {e}"
                result.errors.append(error_msg)
                LOGGER.warning(error_msg)

        return result

    def _get_parent_prefix(self, ip_address: str) -> str | None:
        """
        Get a suitable parent prefix for an IP address.

        Nautobot requires a containing prefix to exist before creating IP addresses.
        This method calculates an appropriate parent prefix size.

        Args:
            ip_address: IP address string (e.g., "10.255.0.1/32" or "10.255.0.1")

        Returns:
            Parent prefix string (e.g., "10.255.0.0/24") or None if invalid
        """
        with contextlib.suppress(ValueError):
            ip_obj = ipaddress.ip_interface(ip_address)
            if ip_obj.version == 4:
                # For IPv4, use /24 as parent (common subnet size)
                return str(ipaddress.ip_network(f"{ip_obj.ip}/24", strict=False))
            # For IPv6, use /64 as parent (common subnet size)
            return str(ipaddress.ip_network(f"{ip_obj.ip}/64", strict=False))
        return None

    def _collect_all_ips_from_config(self, avd_structured_config: dict[str, Any]) -> set[str]:
        """Collect all IP addresses from all interface types in the config."""
        all_ips: set[str] = set()

        # Interface types that can have IP addresses
        interface_types = [
            "loopback_interfaces",
            "vlan_interfaces",
            "ethernet_interfaces",
            "port_channel_interfaces",
        ]

        for intf_type in interface_types:
            for intf in avd_structured_config.get(intf_type, []):
                # Regular IP address
                if ip_addr := intf.get("ip_address"):
                    all_ips.add(ip_addr)
                # Virtual IP addresses (can be string or list)
                ip_virtual = intf.get("ip_address_virtual")
                if isinstance(ip_virtual, str):
                    all_ips.add(ip_virtual)
                elif isinstance(ip_virtual, list):
                    all_ips.update(ip for ip in ip_virtual if isinstance(ip, str))

        return all_ips

    async def sync_prefixes(
        self,
        avd_structured_config: dict[str, Any],
        endpoints: dict[str, str],
    ) -> SyncResult:
        """
        Sync prefixes from AVD config to Nautobot.

        Creates both parent prefixes (e.g., /24) and host prefixes (/32) to ensure
        Nautobot can accept IP address creation within these prefixes.
        """
        result = SyncResult()

        # Collect all IP addresses from the config
        all_ips = self._collect_all_ips_from_config(avd_structured_config)

        # Calculate parent prefixes for all IPs (Nautobot requires parent prefix for IP creation)
        parent_prefixes: set[str] = set()
        host_prefixes: set[str] = set()

        for ip_addr in all_ips:
            with contextlib.suppress(ValueError):
                # Add host prefix (/32 or /128)
                network = ipaddress.ip_interface(ip_addr).network
                host_prefixes.add(str(network))

                # Add parent prefix (/24 or /64)
                parent = self._get_parent_prefix(ip_addr)
                if parent:
                    parent_prefixes.add(parent)

        # Get namespace and status
        namespace_id = await self._ensure_namespace()
        status_id = await self._get_status_id("Active", "ipam.prefix")

        # Sync parent prefixes FIRST (they must exist before child prefixes/IPs)
        for prefix in sorted(parent_prefixes):
            result = result + await self._sync_single_prefix(prefix, namespace_id, status_id, endpoints)

        # Then sync host prefixes
        for prefix in sorted(host_prefixes):
            result = result + await self._sync_single_prefix(prefix, namespace_id, status_id, endpoints)

        return result

    async def _sync_single_prefix(
        self,
        prefix: str,
        namespace_id: str | None,
        status_id: str | None,
        endpoints: dict[str, str],
    ) -> SyncResult:
        """Sync a single prefix to Nautobot."""
        result = SyncResult()

        prefix_data: dict[str, Any] = {"prefix": prefix}
        if namespace_id:
            prefix_data["namespace"] = namespace_id
        if status_id:
            prefix_data["status"] = status_id

        existing = await self._find_nautobot_object(endpoints["prefixes"], prefix=prefix)

        if self.dry_run:
            result.skipped += 1
            return result

        try:
            if existing:
                await self.client.patch(f"{endpoints['prefixes']}{existing['id']}/", prefix_data)
                result.updated += 1
                obj_id = existing["id"]
            else:
                new_prefix = await self.client.post(endpoints["prefixes"], prefix_data)
                result.created += 1
                obj_id = new_prefix["id"]

            self._track_object("prefixes", obj_id)
            await self._apply_managed_tag(endpoints["prefixes"], obj_id)
        except Exception as e:
            error_msg = f"Failed to sync prefix {prefix}: {e}"
            result.errors.append(error_msg)
            LOGGER.warning(error_msg)

        return result

    async def sync_cables(
        self,
        avd_structured_configs: dict[str, dict[str, Any]],
        endpoints: dict[str, str],
    ) -> SyncResult:
        """Sync cables between devices based on link topology."""
        result = SyncResult()

        # Get status for Connected cable
        status_id = await self._get_status_id("Connected", "dcim.cable")

        # Build interface ID cache
        interface_cache: dict[str, dict[str, str]] = {}  # hostname -> {intf_name: intf_id}
        for hostname in avd_structured_configs:
            device = await self._find_nautobot_object(endpoints["devices"], name=hostname)
            if not device:
                continue
            interface_cache[hostname] = {}
            async for intf in self.client.get_all(endpoints["interfaces"], params={"device_id": device["id"]}):
                interface_cache[hostname][intf["name"]] = intf["id"]

        # Find and sync cables from ethernet interface peer info
        for hostname, config in avd_structured_configs.items():
            for intf in config.get("ethernet_interfaces", []):
                peer_device = intf.get("peer")
                peer_intf = intf.get("peer_interface")
                if not peer_device or not peer_intf:
                    continue

                # Get local and peer interface IDs
                local_intf_id = interface_cache.get(hostname, {}).get(intf.get("name"))
                peer_intf_id = interface_cache.get(peer_device, {}).get(peer_intf)

                if not local_intf_id or not peer_intf_id:
                    continue

                # Check for existing cable (Nautobot uses termination_a_id/termination_b_id)
                existing_cable = None
                async for cable in self.client.get_all(
                    endpoints["cables"],
                    params={"termination_a_id": local_intf_id},
                ):
                    existing_cable = cable
                    break

                if not existing_cable:
                    async for cable in self.client.get_all(
                        endpoints["cables"],
                        params={"termination_b_id": local_intf_id},
                    ):
                        existing_cable = cable
                        break

                if self.dry_run:
                    result.skipped += 1
                    continue

                cable_data: dict[str, Any] = {
                    "termination_a_type": "dcim.interface",
                    "termination_a_id": local_intf_id,
                    "termination_b_type": "dcim.interface",
                    "termination_b_id": peer_intf_id,
                }
                if status_id:
                    cable_data["status"] = status_id

                try:
                    if existing_cable:
                        self._track_object("cables", existing_cable["id"])
                        result.skipped += 1
                    else:
                        new_cable = await self.client.post(endpoints["cables"], cable_data)
                        result.created += 1
                        self._track_object("cables", new_cable["id"])
                        await self._apply_managed_tag(endpoints["cables"], new_cable["id"])
                except Exception as e:
                    error_msg = f"Failed to sync cable {hostname}:{intf.get('name')} -> {peer_device}:{peer_intf}: {e}"
                    result.errors.append(error_msg)
                    LOGGER.debug(error_msg)

        return result

    async def reconcile_objects(self) -> SyncResult:
        """Delete Nautobot objects with managed tag that weren't touched during sync."""
        result = SyncResult()
        endpoints = self.mapping.get_nautobot_endpoints()

        # Deletion order (reverse of creation to handle dependencies)
        deletion_order = [
            ("cables", endpoints["cables"]),
            ("ip_addresses", endpoints["ip_addresses"]),
            ("prefixes", endpoints["prefixes"]),
            ("interfaces", endpoints["interfaces"]),
            ("vlans", endpoints["vlans"]),
            ("vrfs", endpoints["vrfs"]),
            ("devices", endpoints["devices"]),
        ]

        for object_type, endpoint in deletion_order:
            touched_ids = self._touched_objects.get(object_type, set())
            params = {"tags": self.managed_tag}

            async for obj in self.client.get_all(endpoint, params=params):
                obj_id = obj.get("id")
                if obj_id and obj_id not in touched_ids:
                    if self.dry_run:
                        result.skipped += 1
                        continue

                    try:
                        await self.client.delete(f"{endpoint}{obj_id}/")
                        result.deleted += 1
                        LOGGER.info("Deleted orphaned %s: %s", object_type, obj.get("name", obj.get("display", obj_id)))
                    except Exception as e:
                        error_msg = f"Failed to delete {object_type} {obj_id}: {e}"
                        result.errors.append(error_msg)
                        LOGGER.warning(error_msg)

        return result

    async def purge_all(self, dry_run: bool | None = None, purge_prerequisites: bool = False) -> SyncResult:
        """
        Delete ALL objects with the managed tag from Nautobot.

        This is a destructive operation that removes all AVD-managed objects
        without performing any sync.

        Args:
            dry_run: If True, don't actually delete, just count what would be deleted.
            purge_prerequisites: If True, also delete locations, device types, platforms, manufacturers, roles.

        Returns:
            SyncResult with deletion counts in the 'deleted' field
        """
        result = SyncResult()
        use_dry_run = dry_run if dry_run is not None else self.dry_run

        tag_id = await self._ensure_managed_tag()
        if not tag_id:
            LOGGER.info("No managed tag '%s' found - nothing to purge", self.managed_tag)
            return result

        endpoints = self.mapping.get_nautobot_endpoints()

        # Endpoints that support 'tags' filter
        tag_filterable = {"cables", "ip_addresses", "interfaces", "prefixes", "vlans", "vrfs", "devices", "device_types", "locations"}
        # Endpoints that don't support 'tags' filter - need to check tags on each object
        non_tag_filterable = {"platforms", "manufacturers", "roles"}

        # Deletion order (reverse dependencies)
        deletion_order: list[tuple[str, str]] = [
            ("cables", endpoints["cables"]),
            ("ip_addresses", endpoints["ip_addresses"]),
            ("interfaces", endpoints["interfaces"]),
            ("prefixes", endpoints["prefixes"]),
            ("vlans", endpoints["vlans"]),
            ("vrfs", endpoints["vrfs"]),
            ("devices", endpoints["devices"]),
        ]

        # Add prerequisites if requested (delete after devices to avoid dependency errors)
        if purge_prerequisites:
            deletion_order.extend(
                [
                    ("device_types", endpoints["device_types"]),
                    ("platforms", endpoints["platforms"]),
                    ("manufacturers", endpoints["manufacturers"]),
                    ("roles", endpoints["roles"]),
                    ("locations", endpoints["locations"]),
                ]
            )

        LOGGER.info("Purging all objects with tag '%s' from Nautobot%s...", self.managed_tag, " (including prerequisites)" if purge_prerequisites else "")

        for object_type, endpoint in deletion_order:
            # Get objects to delete
            to_delete: list[dict[str, Any]] = []

            if object_type in tag_filterable:
                # Use tags filter directly
                to_delete = await self.client.get_all_list(endpoint, params={"tags": self.managed_tag})
            elif object_type in non_tag_filterable:
                # Get all objects and filter by tags manually
                all_objects = await self.client.get_all_list(endpoint)
                for obj in all_objects:
                    obj_tags = obj.get("tags", [])
                    # Tags can be list of dicts with 'name' key or list of strings
                    tag_names = []
                    for t in obj_tags:
                        if isinstance(t, dict):
                            tag_names.append(t.get("name", ""))
                        elif isinstance(t, str):
                            tag_names.append(t)
                    if self.managed_tag in tag_names:
                        to_delete.append(obj)

            if not to_delete:
                continue

            LOGGER.info("Purging %d %s...", len(to_delete), object_type)

            for obj in to_delete:
                obj_id = obj.get("id")
                obj_name = obj.get("name") or obj.get("display") or obj.get("address") or str(obj_id)

                if use_dry_run:
                    LOGGER.info("[DRY RUN] Would delete %s: %s", object_type, obj_name)
                    result.skipped += 1
                    continue

                try:
                    await self.client.delete(f"{endpoint}{obj_id}/")
                    result.deleted += 1
                    LOGGER.debug("Deleted %s: %s", object_type, obj_name)
                except Exception as e:
                    error_msg = f"Failed to delete {object_type} {obj_name}: {e}"
                    result.errors.append(error_msg)
                    LOGGER.warning(error_msg)

        if use_dry_run:
            LOGGER.info("Purge dry run complete: %d objects would be deleted", result.skipped)
        else:
            LOGGER.info("Purge complete: %d deleted, %d errors", result.deleted, len(result.errors))

        return result

    async def sync_all(
        self,
        avd_structured_configs: dict[str, dict[str, Any]],
        node_types: dict[str, str] | None = None,
    ) -> SyncResult:
        """
        Sync all devices and their data from AVD structured configs concurrently.

        This is the main entry point for async sync. It:
        1. Creates prerequisites (manufacturer, device types, roles)
        2. Syncs VRFs and VLANs (sequential - they're prerequisites for interfaces)
        3. Syncs devices and interfaces CONCURRENTLY
        4. Syncs prefixes and cables
        5. Reconciles orphaned objects if enabled

        Args:
            avd_structured_configs: Dict mapping hostname to structured config
            node_types: Optional dict mapping hostname to AVD node type

        Returns:
            Combined SyncResult for all operations
        """
        result = SyncResult()
        node_types = node_types or {}

        # Ensure prerequisites exist
        await self._ensure_prerequisites()
        endpoints = self.mapping.get_nautobot_endpoints()

        # First pass: sync VRFs and VLANs (sequential - they're prerequisites)
        LOGGER.info("Syncing VRFs and VLANs...")
        for config in avd_structured_configs.values():
            result = result + await self.sync_vrfs(config, endpoints)
            result = result + await self.sync_vlans(config, endpoints)

        # Second pass: sync prefixes BEFORE devices/interfaces
        # Nautobot requires parent prefixes to exist before creating IP addresses
        LOGGER.info("Syncing prefixes (required for IP addresses)...")
        for config in avd_structured_configs.values():
            result = result + await self.sync_prefixes(config, endpoints)

        # Third pass: sync devices and interfaces CONCURRENTLY
        LOGGER.info("Syncing %d devices concurrently (max %d concurrent)...", len(avd_structured_configs), self.max_concurrent)
        tasks = [self._sync_single_device(hostname, config, node_types.get(hostname), endpoints) for hostname, config in avd_structured_configs.items()]

        device_results = await asyncio.gather(*tasks, return_exceptions=True)
        for i, device_result in enumerate(device_results):
            if isinstance(device_result, SyncResult):
                result = result + device_result
            elif isinstance(device_result, Exception):
                hostname = list(avd_structured_configs.keys())[i]
                error_msg = f"Device sync failed for {hostname}: {device_result}"
                result.errors.append(error_msg)
                LOGGER.error(error_msg)

        # Fourth pass: sync cables
        LOGGER.info("Syncing cables...")
        result = result + await self.sync_cables(avd_structured_configs, endpoints)

        # Reconcile objects if enabled
        if self.reconcile:
            LOGGER.info("Reconciling Nautobot objects (deleting orphaned objects)...")
            reconcile_result = await self.reconcile_objects()
            LOGGER.info(
                "Reconciliation complete: %d deleted, %d skipped, %d errors",
                reconcile_result.deleted,
                reconcile_result.skipped,
                len(reconcile_result.errors),
            )
            result = result + reconcile_result

        LOGGER.info(
            "Async sync complete: %d created, %d updated, %d skipped, %d deleted, %d errors",
            result.created,
            result.updated,
            result.skipped,
            result.deleted,
            len(result.errors),
        )

        return result
