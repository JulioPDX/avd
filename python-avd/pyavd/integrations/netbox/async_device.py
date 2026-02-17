# Copyright (c) 2023-2026 Arista Networks, Inc.
# Use of this source code is governed by the Apache License 2.0
# that can be found in the LICENSE file.
"""Async device sync methods for AVD to NetBox synchronization."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .models import DEFAULT_DEVICE_TYPE, DEFAULT_MANUFACTURER, DEFAULT_PLATFORM, NODE_TYPE_TO_DEVICE_ROLE, AVDNetBoxMapping, SyncResult
from .transforms import slugify

if TYPE_CHECKING:
    import asyncio

    from .client import AsyncNetBoxClient

LOGGER = logging.getLogger(__name__)


class AsyncDeviceMixin:
    """Mixin class providing async device sync methods."""

    client: AsyncNetBoxClient
    mapping: AVDNetBoxMapping
    dry_run: bool
    create_prerequisites: bool
    _semaphore: asyncio.Semaphore
    _prerequisite_lock: asyncio.Lock
    _touched_objects: dict[str, set[int]]

    # These methods are provided by AsyncHelpersMixin
    async def _get_or_cache(self, cache_key: str, endpoint: str, lookup_field: str) -> dict[str, Any]: ...
    async def _find_netbox_object(self, endpoint: str, **kwargs: Any) -> dict[str, Any] | None: ...
    async def _get_site_for_hostname(self, hostname: str) -> dict[str, Any] | None: ...
    async def _apply_managed_tag(self, endpoint: str, obj_id: int) -> bool: ...
    def _track_object(self, obj_type: str, obj_id: int) -> None: ...
    def _get_library_model_name(self, platform_name: str) -> str: ...
    async def _fetch_devicetype_from_library(self, model_name: str) -> dict[str, Any] | None: ...
    async def _create_devicetype_from_library(self, library_def: dict[str, Any], manufacturer_id: int) -> dict[str, Any] | None: ...

    # This method is provided by AsyncInterfaceMixin
    async def _sync_interfaces_async(self, avd_structured_config: dict[str, Any], endpoints: dict[str, str]) -> SyncResult: ...

    async def _sync_single_device(
        self,
        hostname: str,
        config: dict[str, Any],
        node_type: str | None,
        endpoints: dict[str, str],
    ) -> SyncResult:
        """Sync a single device and its interfaces (used for concurrent sync)."""
        async with self._semaphore:
            result = SyncResult()

            try:
                # Sync device
                device_result = await self._sync_device_async(config, node_type, endpoints)
                result = result + device_result

                # Sync interfaces for this device
                interfaces_result = await self._sync_interfaces_async(config, endpoints)
                result = result + interfaces_result

                LOGGER.debug("Synced device %s: created=%d, updated=%d", hostname, result.created, result.updated)
            except Exception as e:
                error_msg = f"Failed to sync device {hostname}: {e}"
                result.errors.append(error_msg)
                LOGGER.warning(error_msg)

            return result

    async def _sync_device_async(
        self,
        avd_structured_config: dict[str, Any],
        node_type: str | None,
        endpoints: dict[str, str],
    ) -> SyncResult:
        """Sync a single device to NetBox."""
        result = SyncResult()
        hostname = avd_structured_config.get("hostname")
        if not hostname:
            return result

        # Get or create site for this device
        site = await self._get_site_for_hostname(hostname)
        site_id = site["id"] if site else None

        # Determine device role
        if not node_type:
            node_type = self._infer_node_type(hostname)
        role_name = NODE_TYPE_TO_DEVICE_ROLE.get(node_type, "Unknown")
        role_slug = slugify(role_name)

        # Build device data
        device_data: dict[str, Any] = {"name": hostname, "status": "active"}

        if site_id:
            device_data["site"] = site_id

        # Get role ID
        roles_cache = await self._get_or_cache("device_roles", endpoints["device_roles"], "slug")
        role = roles_cache.get(role_slug)
        if role:
            device_data["role"] = role["id"]

        # Get device type from metadata.platform
        metadata = avd_structured_config.get("metadata", {})
        platform_name = metadata.get("platform")

        # Get or create device type
        types_cache = await self._get_or_cache("device_types", endpoints["device_types"], "slug")
        if platform_name:
            await self._handle_device_type(platform_name, device_data, types_cache, endpoints)
        else:
            default_type = types_cache.get(DEFAULT_DEVICE_TYPE["slug"])
            if default_type:
                device_data["device_type"] = default_type["id"]

        # Set EOS platform
        await self._handle_platform(device_data, endpoints)

        # Check for existing device
        existing = await self._find_netbox_object(endpoints["devices"], name=hostname)

        if self.dry_run:
            result.skipped += 1
            return result

        try:
            if existing:
                await self.client.patch(f"{endpoints['devices']}{existing['id']}/", device_data)
                result.updated += 1
                device_id = existing["id"]
            else:
                new_device = await self.client.post(endpoints["devices"], device_data)
                result.created += 1
                device_id = new_device["id"]

            self._track_object("devices", device_id)
            await self._apply_managed_tag(endpoints["devices"], device_id)
        except Exception as e:
            error_msg = f"Failed to sync device {hostname}: {e}"
            result.errors.append(error_msg)
            LOGGER.warning(error_msg)

        return result

    async def _handle_device_type(
        self,
        platform_name: str,
        device_data: dict[str, Any],
        types_cache: dict[str, Any],
        endpoints: dict[str, str],
    ) -> None:
        """Handle device type creation/lookup."""
        library_model_name = self._get_library_model_name(platform_name)  # pylint: disable=assignment-from-no-return
        device_type_slug = slugify(library_model_name)
        device_type = types_cache.get(device_type_slug)

        if device_type:
            device_data["device_type"] = device_type["id"]
        elif self.create_prerequisites and not self.dry_run:
            async with self._prerequisite_lock:
                # Check cache again after acquiring lock
                device_type = types_cache.get(device_type_slug)
                if device_type:
                    device_data["device_type"] = device_type["id"]
                else:
                    manufacturers_cache = await self._get_or_cache("manufacturers", endpoints["manufacturers"], "slug")
                    manufacturer = manufacturers_cache.get(DEFAULT_MANUFACTURER["slug"])
                    manufacturer_id = manufacturer["id"] if manufacturer else None

                    library_def = await self._fetch_devicetype_from_library(library_model_name)
                    if library_def and manufacturer_id:
                        new_device_type = await self._create_devicetype_from_library(library_def, manufacturer_id)
                        if new_device_type:
                            device_data["device_type"] = new_device_type["id"]
                            types_cache[device_type_slug] = new_device_type
                        else:
                            await self._create_simple_device_type(library_model_name, device_type_slug, manufacturer_id, device_data, types_cache, endpoints)
                    else:
                        await self._create_simple_device_type(library_model_name, device_type_slug, manufacturer_id, device_data, types_cache, endpoints)
        else:
            LOGGER.debug("Device type '%s' not found, using default", platform_name)
            default_type = types_cache.get(DEFAULT_DEVICE_TYPE["slug"])
            if default_type:
                device_data["device_type"] = default_type["id"]

    async def _create_simple_device_type(
        self,
        model_name: str,
        slug: str,
        manufacturer_id: int | None,
        device_data: dict[str, Any],
        types_cache: dict[str, Any],
        endpoints: dict[str, str],
    ) -> None:
        """Create a simple device type without library specs."""
        LOGGER.info("Creating device type: %s", model_name)
        device_type_data: dict[str, Any] = {"model": model_name, "slug": slug}
        if manufacturer_id:
            device_type_data["manufacturer"] = manufacturer_id
        new_device_type = await self.client.post(endpoints["device_types"], device_type_data)
        device_data["device_type"] = new_device_type["id"]
        types_cache[slug] = new_device_type

    async def _handle_platform(self, device_data: dict[str, Any], endpoints: dict[str, str]) -> None:
        """Handle EOS platform creation/lookup."""
        platforms_cache = await self._get_or_cache("platforms", endpoints["platforms"], "slug")
        eos_platform = platforms_cache.get(DEFAULT_PLATFORM["slug"])

        if eos_platform:
            device_data["platform"] = eos_platform["id"]
        elif self.create_prerequisites and not self.dry_run:
            async with self._prerequisite_lock:
                eos_platform = platforms_cache.get(DEFAULT_PLATFORM["slug"])
                if eos_platform:
                    device_data["platform"] = eos_platform["id"]
                else:
                    LOGGER.info("Creating platform: %s", DEFAULT_PLATFORM["name"])
                    new_platform = await self.client.post(endpoints["platforms"], DEFAULT_PLATFORM)
                    device_data["platform"] = new_platform["id"]
                    platforms_cache[DEFAULT_PLATFORM["slug"]] = new_platform

    def _infer_node_type(self, hostname: str) -> str:
        """Infer AVD node type from hostname patterns."""
        hostname_lower = hostname.lower()

        if "spine" in hostname_lower:
            if "l2" in hostname_lower:
                return "l2spine"
            if "l3" in hostname_lower:
                return "l3spine"
            return "spine"
        if "leaf" in hostname_lower:
            return "l2leaf" if hostname_lower.endswith("c") else "l3leaf"
        if "-pe" in hostname_lower or hostname_lower.startswith("pe"):
            return "pe"
        if "-rr" in hostname_lower or hostname_lower.startswith("rr"):
            return "rr"
        if "-p" in hostname_lower:
            return "p"
        if "wan" in hostname_lower:
            return "wan_router"

        return "l3leaf"
