# Copyright (c) 2023-2026 Arista Networks, Inc.
# Use of this source code is governed by the Apache License 2.0
# that can be found in the LICENSE file.
"""
Async AVD to NetBox Synchronization Logic.

Provides high-performance async synchronization from AVD structured configuration
data to NetBox using concurrent requests.
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
from .models import AVDNetBoxMapping, SyncResult

if TYPE_CHECKING:
    from .client import AsyncNetBoxClient

LOGGER = logging.getLogger(__name__)


class AsyncAVDNetBoxSync(AsyncHelpersMixin, AsyncDeviceMixin, AsyncInterfaceMixin):
    """
    Async synchronization of AVD structured configuration to NetBox.

    Uses concurrent requests to significantly speed up synchronization.
    Typical speedup is 4-8x compared to synchronous version.

    Args:
        client: Async NetBox API client instance
        mapping: Field mapping configuration (uses defaults if not provided)
        dry_run: If True, don't make actual changes to NetBox
        site_name: Default NetBox site name for new objects
        site_mapping: Dict mapping hostname prefix to site name
        create_prerequisites: If True, create missing sites/roles/types in NetBox
        managed_tag: Tag name to mark AVD-managed objects (default: "avd-managed")
        reconcile: If True, delete objects with managed_tag that weren't synced
        max_concurrent: Maximum concurrent operations per phase (default: 10)
        devicetype_library_url: Base URL for NetBox Community Device Type Library (set to None to disable)
        platform_mapping: Dict mapping AVD platform names to library device type model names
    """

    DEFAULT_MANAGED_TAG = "avd-managed"

    def __init__(
        self,
        client: AsyncNetBoxClient,
        mapping: AVDNetBoxMapping | None = None,
        *,
        dry_run: bool = False,
        site_name: str | None = None,
        site_mapping: dict[str, str] | None = None,
        create_prerequisites: bool = True,
        managed_tag: str | None = None,
        reconcile: bool = False,
        max_concurrent: int = 10,
        devicetype_library_url: str | None = None,
        platform_mapping: dict[str, str] | None = None,
    ) -> None:
        self.client = client
        self.mapping = mapping or AVDNetBoxMapping()
        self.dry_run = dry_run
        self.site_name = site_name
        self.site_mapping = site_mapping or {}
        self.create_prerequisites = create_prerequisites
        self.managed_tag = managed_tag or self.DEFAULT_MANAGED_TAG
        self.reconcile = reconcile
        self.max_concurrent = max_concurrent
        self.devicetype_library_url = devicetype_library_url
        self.platform_mapping = platform_mapping or {}
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._cache: dict[str, dict[str, Any]] = {}
        self._prerequisites_created = False
        self._site_cache: dict[str, dict[str, Any]] = {}
        self._managed_tag_id: int | None = None
        self._cache_lock = asyncio.Lock()
        self._prerequisite_lock = asyncio.Lock()
        self._devicetype_library_cache: dict[str, dict[str, Any] | None] = {}
        self._library_lock = asyncio.Lock()

        # Track objects touched during sync for reconciliation
        self._touched_objects: dict[str, set[int]] = {
            "devices": set(),
            "interfaces": set(),
            "vlans": set(),
            "vrfs": set(),
            "ip_addresses": set(),
            "prefixes": set(),
            "cables": set(),
            "asns": set(),
        }

    # =========================================================================
    # VLAN, VRF, Prefix, ASN, and Cable sync methods
    # =========================================================================
    # VLAN, VRF, Prefix, ASN, Cable sync methods (these stay in main class)
    # =========================================================================

    async def sync_vlans(
        self,
        avd_structured_config: dict[str, Any],
        endpoints: dict[str, str],
    ) -> SyncResult:
        """Sync VLANs from AVD config to NetBox."""
        result = SyncResult()
        hostname = avd_structured_config.get("hostname", "")
        vlans = avd_structured_config.get("vlans", [])

        if not vlans:
            return result

        site = await self._get_site_for_hostname(hostname)
        site_id = site["id"] if site else None

        for vlan in vlans:
            vlan_id = vlan.get("id")
            vlan_name = vlan.get("name", f"VLAN{vlan_id}")
            if not vlan_id:
                continue

            vlan_data: dict[str, Any] = {"vid": vlan_id, "name": vlan_name, "status": "active"}
            if site_id:
                vlan_data["site"] = site_id

            # Check for existing VLAN at this site
            params: dict[str, Any] = {"vid": vlan_id}
            if site_id:
                params["site_id"] = site_id
            existing = await self._find_netbox_object(endpoints["vlans"], **params)

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
        """Sync VRFs from AVD config to NetBox."""
        result = SyncResult()
        vrfs = avd_structured_config.get("vrfs", [])

        for vrf in vrfs:
            vrf_name = vrf.get("name")
            if not vrf_name or vrf_name in ("default", "MGMT", "management"):
                continue

            vrf_data: dict[str, Any] = {"name": vrf_name}
            if "rd" in vrf:
                vrf_data["rd"] = vrf["rd"]

            existing = await self._find_netbox_object(endpoints["vrfs"], name=vrf_name)

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

    async def sync_prefixes(
        self,
        avd_structured_config: dict[str, Any],
        endpoints: dict[str, str],
    ) -> SyncResult:
        """Sync prefixes from AVD config to NetBox."""
        result = SyncResult()

        # Extract prefixes from various sources in the config
        prefixes_to_sync: set[str] = set()

        # From loopback interfaces
        for intf in avd_structured_config.get("loopback_interfaces", []):
            if ip_addr := intf.get("ip_address"):
                with contextlib.suppress(ValueError):
                    network = ipaddress.ip_interface(ip_addr).network
                    prefixes_to_sync.add(str(network))

        # From VLAN interfaces
        for intf in avd_structured_config.get("vlan_interfaces", []):
            if ip_addr := intf.get("ip_address"):
                with contextlib.suppress(ValueError):
                    network = ipaddress.ip_interface(ip_addr).network
                    prefixes_to_sync.add(str(network))

        for prefix in prefixes_to_sync:
            prefix_data: dict[str, Any] = {"prefix": prefix, "status": "active"}
            existing = await self._find_netbox_object(endpoints["prefixes"], prefix=prefix)

            if self.dry_run:
                result.skipped += 1
                continue

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

    async def sync_asns(
        self,
        avd_structured_config: dict[str, Any],
        endpoints: dict[str, str],
    ) -> SyncResult:
        """Sync ASNs from AVD config to NetBox."""
        result = SyncResult()
        asns_to_sync: set[int] = set()

        # Extract ASNs from BGP config
        if bgp_as := avd_structured_config.get("router_bgp", {}).get("as"):
            with contextlib.suppress(ValueError, TypeError):
                asns_to_sync.add(int(bgp_as))

        for asn in asns_to_sync:
            asn_data: dict[str, Any] = {"asn": asn}

            # Get RIR for ASN assignment
            rir_cache = await self._get_or_cache("rirs", endpoints["rirs"], "slug")
            if "rfc-6996-private" in rir_cache:
                asn_data["rir"] = rir_cache["rfc-6996-private"]["id"]
            elif rir_cache:
                asn_data["rir"] = next(iter(rir_cache.values()))["id"]

            existing = await self._find_netbox_object(endpoints["asns"], asn=asn)

            if self.dry_run:
                result.skipped += 1
                continue

            try:
                if existing:
                    await self.client.patch(f"{endpoints['asns']}{existing['id']}/", asn_data)
                    result.updated += 1
                    obj_id = existing["id"]
                else:
                    new_asn = await self.client.post(endpoints["asns"], asn_data)
                    result.created += 1
                    obj_id = new_asn["id"]

                self._track_object("asns", obj_id)
                await self._apply_managed_tag(endpoints["asns"], obj_id)
            except Exception as e:
                error_msg = f"Failed to sync ASN {asn}: {e}"
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

        # Build interface ID cache
        interface_cache: dict[str, dict[str, int]] = {}  # hostname -> {intf_name: intf_id}
        for hostname in avd_structured_configs:
            device = await self._find_netbox_object(endpoints["devices"], name=hostname)
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

                # Check for existing cable
                existing_cable = None
                async for cable in self.client.get_all(
                    endpoints["cables"],
                    params={"termination_a_id": local_intf_id, "termination_a_type": "dcim.interface"},
                ):
                    existing_cable = cable
                    break

                if not existing_cable:
                    async for cable in self.client.get_all(
                        endpoints["cables"],
                        params={"termination_b_id": local_intf_id, "termination_b_type": "dcim.interface"},
                    ):
                        existing_cable = cable
                        break

                if self.dry_run:
                    result.skipped += 1
                    continue

                cable_data = {
                    "a_terminations": [{"object_type": "dcim.interface", "object_id": local_intf_id}],
                    "b_terminations": [{"object_type": "dcim.interface", "object_id": peer_intf_id}],
                    "status": "connected",
                }

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
        """Delete NetBox objects with managed tag that weren't touched during sync."""
        result = SyncResult()
        endpoints = self.mapping.get_netbox_endpoints()

        # Deletion order (reverse of creation to handle dependencies)
        deletion_order = [
            ("cables", endpoints["cables"]),
            ("ip_addresses", endpoints["ip_addresses"]),
            ("prefixes", endpoints["prefixes"]),
            ("interfaces", endpoints["interfaces"]),
            ("vlans", endpoints["vlans"]),
            ("vrfs", endpoints["vrfs"]),
            ("asns", endpoints["asns"]),
            ("devices", endpoints["devices"]),
        ]

        for object_type, endpoint in deletion_order:
            touched_ids = self._touched_objects.get(object_type, set())
            params = {"tag": self.managed_tag}

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

    async def purge_all(self, dry_run: bool | None = None) -> SyncResult:
        """
        Delete ALL objects with the managed tag from NetBox.

        This is a destructive operation that removes all AVD-managed objects
        without performing any sync. Useful for cleaning up a NetBox instance
        before migrating to a different source of truth or starting fresh.

        Objects are deleted in reverse dependency order to avoid foreign key errors:
        cables → IPs → interfaces → prefixes → VLANs → VRFs → ASNs → devices

        Args:
            dry_run: If True, don't actually delete, just count what would be deleted.
                    Overrides instance dry_run setting.

        Returns:
            SyncResult with deletion counts in the 'deleted' field
        """
        result = SyncResult()
        use_dry_run = dry_run if dry_run is not None else self.dry_run

        tag_id = await self._ensure_managed_tag()
        if not tag_id:
            LOGGER.info("No managed tag '%s' found - nothing to purge", self.managed_tag)
            return result

        endpoints = self.mapping.get_netbox_endpoints()

        # Deletion order (reverse dependencies)
        deletion_order = [
            ("cables", endpoints["cables"]),
            ("ip_addresses", endpoints["ip_addresses"]),
            ("interfaces", endpoints["interfaces"]),
            ("prefixes", endpoints["prefixes"]),
            ("vlans", endpoints["vlans"]),
            ("vrfs", endpoints["vrfs"]),
            ("asns", endpoints["asns"]),
            ("devices", endpoints["devices"]),
        ]

        LOGGER.info("Purging all objects with tag '%s' from NetBox...", self.managed_tag)

        for object_type, endpoint in deletion_order:
            params = {"tag": self.managed_tag}
            to_delete = await self.client.get_all_list(endpoint, params=params)

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
        3. Syncs devices and interfaces CONCURRENTLY (main performance improvement)
        4. Syncs prefixes, ASNs, and cables
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
        endpoints = self.mapping.get_netbox_endpoints()

        # First pass: sync VRFs and VLANs (sequential - they're prerequisites)
        LOGGER.info("Syncing VRFs and VLANs...")
        for config in avd_structured_configs.values():
            result = result + await self.sync_vrfs(config, endpoints)
            result = result + await self.sync_vlans(config, endpoints)

        # Second pass: sync devices and interfaces CONCURRENTLY
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

        # Third pass: sync prefixes and ASNs
        LOGGER.info("Syncing prefixes and ASNs...")
        for config in avd_structured_configs.values():
            result = result + await self.sync_prefixes(config, endpoints)
            result = result + await self.sync_asns(config, endpoints)

        # Fourth pass: sync cables
        LOGGER.info("Syncing cables...")
        result = result + await self.sync_cables(avd_structured_configs, endpoints)

        # Reconcile objects if enabled
        if self.reconcile:
            LOGGER.info("Reconciling NetBox objects (deleting orphaned objects)...")
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
