# Copyright (c) 2023-2026 Arista Networks, Inc.
# Use of this source code is governed by the Apache License 2.0
# that can be found in the LICENSE file.
"""Async device sync methods for AVD to Nautobot synchronization."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .models import DEFAULT_DEVICE_TYPE, DEFAULT_MANUFACTURER, DEFAULT_PLATFORM, NODE_TYPE_TO_DEVICE_ROLE, AVDNautobotMapping, SyncResult

if TYPE_CHECKING:
    import asyncio

    from .client import AsyncNautobotClient

LOGGER = logging.getLogger(__name__)


class AsyncDeviceMixin:
    """Mixin class providing async device sync methods."""

    client: AsyncNautobotClient
    mapping: AVDNautobotMapping
    dry_run: bool
    create_prerequisites: bool
    _semaphore: asyncio.Semaphore
    _prerequisite_lock: asyncio.Lock
    _touched_objects: dict[str, set[str]]

    # Type annotations for methods provided by other mixins (AsyncHelpersMixin)
    # Note: These are TYPE_CHECKING only stubs - do not implement as that would shadow real implementations
    if TYPE_CHECKING:

        async def _get_or_cache(self, cache_key: str, endpoint: str, lookup_field: str) -> dict[str, Any]: ...
        async def _find_nautobot_object(self, endpoint: str, **kwargs: Any) -> dict[str, Any] | None: ...
        async def _get_location_for_hostname(self, hostname: str) -> dict[str, Any] | None: ...
        async def _apply_managed_tag(self, endpoint: str, obj_id: str) -> bool: ...
        async def _ensure_managed_tag(self) -> str | None: ...
        async def _get_status_id(self, status_name: str, content_type: str) -> str | None: ...
        def _track_object(self, obj_type: str, obj_id: str) -> None: ...
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
        """Sync a single device to Nautobot."""
        result = SyncResult()
        hostname = avd_structured_config.get("hostname")
        if not hostname:
            return result

        # Get or create location for this device
        location = await self._get_location_for_hostname(hostname)
        location_id = location["id"] if location else None

        # Determine device role
        if not node_type:
            node_type = self._infer_node_type(hostname)
        role_name = NODE_TYPE_TO_DEVICE_ROLE.get(node_type, "Unknown")

        # Get status ID for "Active"
        status_id = await self._get_status_id("Active", "dcim.device")

        # Build device data
        device_data: dict[str, Any] = {"name": hostname}
        if status_id:
            device_data["status"] = status_id
        if location_id:
            device_data["location"] = location_id

        # Get role ID from extras/roles
        roles_cache = await self._get_or_cache("roles", endpoints["roles"], "name")
        role = roles_cache.get(role_name)
        if role:
            device_data["role"] = role["id"]

        # Get device type from metadata.platform
        metadata = avd_structured_config.get("metadata", {})
        platform_name = metadata.get("platform")

        # Get or create device type
        types_cache = await self._get_or_cache("device_types", endpoints["device_types"], "model")
        if platform_name:
            await self._handle_device_type(platform_name, device_data, types_cache, endpoints)
        else:
            default_type = types_cache.get(DEFAULT_DEVICE_TYPE["model"])
            if default_type:
                device_data["device_type"] = default_type["id"]

        # Set EOS platform
        await self._handle_platform(device_data, endpoints)

        # Check for existing device
        existing = await self._find_nautobot_object(endpoints["devices"], name=hostname)

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
        device_type = types_cache.get(platform_name)

        if device_type:
            device_data["device_type"] = device_type["id"]
        elif self.create_prerequisites and not self.dry_run:
            async with self._prerequisite_lock:
                # Check cache again after acquiring lock
                device_type = types_cache.get(platform_name)
                if device_type:
                    device_data["device_type"] = device_type["id"]
                else:
                    manufacturers_cache = await self._get_or_cache("manufacturers", endpoints["manufacturers"], "name")
                    manufacturer = manufacturers_cache.get(DEFAULT_MANUFACTURER["name"])
                    manufacturer_id = manufacturer["id"] if manufacturer else None

                    if manufacturer_id:
                        LOGGER.info("Creating device type: %s", platform_name)
                        # Include managed tag
                        tag_id = await self._ensure_managed_tag()
                        device_type_data: dict[str, Any] = {"model": platform_name, "manufacturer": manufacturer_id}
                        if tag_id:
                            device_type_data["tags"] = [tag_id]
                        new_device_type = await self.client.post(endpoints["device_types"], device_type_data)
                        device_data["device_type"] = new_device_type["id"]
                        types_cache[platform_name] = new_device_type
        else:
            LOGGER.debug("Device type '%s' not found, using default", platform_name)
            default_type = types_cache.get(DEFAULT_DEVICE_TYPE["model"])
            if default_type:
                device_data["device_type"] = default_type["id"]

    async def _handle_platform(self, device_data: dict[str, Any], endpoints: dict[str, str]) -> None:
        """Handle EOS platform creation/lookup."""
        platforms_cache = await self._get_or_cache("platforms", endpoints["platforms"], "name")
        eos_platform = platforms_cache.get(DEFAULT_PLATFORM["name"])

        if eos_platform:
            device_data["platform"] = eos_platform["id"]
        elif self.create_prerequisites and not self.dry_run:
            async with self._prerequisite_lock:
                eos_platform = platforms_cache.get(DEFAULT_PLATFORM["name"])
                if eos_platform:
                    device_data["platform"] = eos_platform["id"]
                else:
                    LOGGER.info("Creating platform: %s", DEFAULT_PLATFORM["name"])
                    # Note: Nautobot platforms don't support tags
                    manufacturers_cache = await self._get_or_cache("manufacturers", endpoints["manufacturers"], "name")
                    manufacturer = manufacturers_cache.get(DEFAULT_MANUFACTURER["name"])
                    platform_data: dict[str, Any] = {"name": DEFAULT_PLATFORM["name"]}
                    if manufacturer:
                        platform_data["manufacturer"] = manufacturer["id"]
                    new_platform = await self.client.post(endpoints["platforms"], platform_data)
                    device_data["platform"] = new_platform["id"]
                    platforms_cache[DEFAULT_PLATFORM["name"]] = new_platform

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
