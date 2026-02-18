# Copyright (c) 2023-2026 Arista Networks, Inc.
# Use of this source code is governed by the Apache License 2.0
# that can be found in the LICENSE file.
"""Async helper methods for AVD to Nautobot synchronization."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .models import DEFAULT_DEVICE_TYPE, DEFAULT_LOCATION_TYPE, DEFAULT_MANUFACTURER, DEFAULT_NAMESPACE, NODE_TYPE_TO_DEVICE_ROLE, AVDNautobotMapping
from .transforms import get_nested_value

if TYPE_CHECKING:
    import asyncio

    from .client import AsyncNautobotClient

LOGGER = logging.getLogger(__name__)


class AsyncHelpersMixin:
    """Mixin class providing async helper methods for Nautobot sync."""

    client: AsyncNautobotClient
    mapping: AVDNautobotMapping
    dry_run: bool
    create_prerequisites: bool
    managed_tag: str
    location_name: str | None
    location_mapping: dict[str, str]
    _cache: dict[str, dict[str, Any]]
    _cache_lock: asyncio.Lock
    _location_cache: dict[str, dict[str, Any]]
    _status_cache: dict[str, dict[str, Any]]  # {content_type:status_name: status_obj}
    _namespace_id: str | None
    _managed_tag_id: str | None
    _prerequisites_created: bool
    _prerequisite_lock: asyncio.Lock
    _touched_objects: dict[str, set[str]]

    async def _get_or_cache(self, cache_key: str, endpoint: str, lookup_field: str) -> dict[str, Any]:
        """Get cached lookup table or fetch from Nautobot."""
        async with self._cache_lock:
            if cache_key not in self._cache:
                self._cache[cache_key] = {}
                async for obj in self.client.get_all(endpoint):
                    key = get_nested_value(obj, lookup_field)
                    if key:
                        self._cache[cache_key][key] = obj
        return self._cache[cache_key]

    async def _find_nautobot_object(self, endpoint: str, **kwargs: Any) -> dict[str, Any] | None:
        """Find a single object in Nautobot by query parameters."""
        response = await self.client.get(endpoint, params=kwargs)
        results = response.get("results", [])
        return results[0] if results else None

    async def _get_status_id(self, status_name: str, content_type: str) -> str | None:
        """Get status UUID by name and content type."""
        cache_key = f"{content_type}:{status_name}"
        if cache_key in self._status_cache:
            return self._status_cache[cache_key].get("id")

        endpoints = self.mapping.get_nautobot_endpoints()
        async for status in self.client.get_all(endpoints["statuses"], params={"name": status_name}):
            content_types = status.get("content_types", [])
            if content_type in content_types:
                self._status_cache[cache_key] = status
                return status["id"]
        return None

    async def _ensure_namespace(self) -> str | None:
        """Ensure the default namespace exists and return its ID."""
        if self._namespace_id:
            return self._namespace_id

        endpoints = self.mapping.get_nautobot_endpoints()
        existing = await self._find_nautobot_object(endpoints["namespaces"], name=DEFAULT_NAMESPACE["name"])

        if existing:
            self._namespace_id = existing["id"]
            return self._namespace_id

        if self.create_prerequisites and not self.dry_run:
            namespace_data = {"name": DEFAULT_NAMESPACE["name"]}
            new_namespace = await self.client.post(endpoints["namespaces"], namespace_data)
            self._namespace_id = new_namespace["id"]
            LOGGER.info("Created namespace: %s", DEFAULT_NAMESPACE["name"])
            return self._namespace_id

        return None

    async def _ensure_location_type(self) -> str | None:
        """Ensure the default location type exists with correct content_types and return its ID."""
        endpoints = self.mapping.get_nautobot_endpoints()
        required_content_types = {"dcim.device", "ipam.vlan", "ipam.prefix"}
        existing = await self._find_nautobot_object(endpoints["location_types"], name=DEFAULT_LOCATION_TYPE["name"])

        if existing:
            # Check if existing location type has the required content_types
            current_content_types = set(existing.get("content_types", []))
            if not required_content_types.issubset(current_content_types) and self.create_prerequisites and not self.dry_run:
                # Update the location type to add missing content_types
                updated_content_types = list(current_content_types | required_content_types)
                await self.client.patch(
                    f"{endpoints['location_types']}{existing['id']}/",
                    {"content_types": updated_content_types},
                )
                LOGGER.info("Updated location type %s with content_types: %s", DEFAULT_LOCATION_TYPE["name"], updated_content_types)
            return existing["id"]

        if self.create_prerequisites and not self.dry_run:
            location_type_data = {
                "name": DEFAULT_LOCATION_TYPE["name"],
                "nestable": True,
                # Content types that can be associated with this location type
                "content_types": list(required_content_types),
            }
            new_type = await self.client.post(endpoints["location_types"], location_type_data)
            LOGGER.info("Created location type: %s", DEFAULT_LOCATION_TYPE["name"])
            return new_type["id"]

        return None

    async def _ensure_prerequisites(self) -> None:
        """Ensure required objects exist in Nautobot (manufacturer, device type, roles, statuses)."""
        if self._prerequisites_created or not self.create_prerequisites:
            return

        endpoints = self.mapping.get_nautobot_endpoints()

        # Get managed tag ID to include in created objects
        tag_id = await self._ensure_managed_tag()

        # Ensure namespace exists
        await self._ensure_namespace()

        # Ensure location type exists
        await self._ensure_location_type()

        # Create manufacturer if needed
        # Note: Nautobot manufacturers don't support tags
        existing = await self._find_nautobot_object(endpoints["manufacturers"], name=DEFAULT_MANUFACTURER["name"])
        if not existing and not self.dry_run:
            await self.client.post(endpoints["manufacturers"], DEFAULT_MANUFACTURER)
            LOGGER.info("Created manufacturer: %s", DEFAULT_MANUFACTURER["name"])

        # Create default device type if needed (with tag)
        existing = await self._find_nautobot_object(endpoints["device_types"], model=DEFAULT_DEVICE_TYPE["model"])
        if not existing and not self.dry_run:
            mfr = await self._find_nautobot_object(endpoints["manufacturers"], name=DEFAULT_MANUFACTURER["name"])
            if mfr:
                device_type_data = {**DEFAULT_DEVICE_TYPE, "manufacturer": mfr["id"]}
                if tag_id:
                    device_type_data["tags"] = [tag_id]
                await self.client.post(endpoints["device_types"], device_type_data)
                LOGGER.info("Created device type: %s", DEFAULT_DEVICE_TYPE["model"])

        # Create device roles in extras/roles
        # Note: Nautobot roles don't support tags
        for role_name in set(NODE_TYPE_TO_DEVICE_ROLE.values()):
            existing = await self._find_nautobot_object(endpoints["roles"], name=role_name)
            if not existing and not self.dry_run:
                role_data: dict[str, Any] = {
                    "name": role_name,
                    "content_types": ["dcim.device"],
                    "color": "0077ff",
                }
                await self.client.post(endpoints["roles"], role_data)
                LOGGER.info("Created device role: %s", role_name)

        self._prerequisites_created = True

    async def _ensure_managed_tag(self) -> str | None:
        """Ensure the managed tag exists and return its ID."""
        if self._managed_tag_id:
            return self._managed_tag_id

        endpoints = self.mapping.get_nautobot_endpoints()

        existing = await self._find_nautobot_object(endpoints["tags"], name=self.managed_tag)
        if existing:
            self._managed_tag_id = existing["id"]
            return self._managed_tag_id

        if not self.dry_run:
            try:
                # Note: Not all Nautobot models support tags. Manufacturer, Platform, and Role
                # models do not have a tags field in Nautobot, so we only include content types
                # that actually support tags.
                tag_data = {
                    "name": self.managed_tag,
                    "color": "0077ff",
                    "content_types": [
                        "dcim.device",
                        "dcim.devicetype",
                        "dcim.interface",
                        "dcim.cable",
                        "dcim.location",
                        "ipam.ipaddress",
                        "ipam.vlan",
                        "ipam.vrf",
                        "ipam.prefix",
                    ],
                }
                tag = await self.client.post(endpoints["tags"], tag_data)
                self._managed_tag_id = tag["id"]
                LOGGER.info("Created managed tag: %s", self.managed_tag)
            except Exception as e:
                LOGGER.warning("Failed to create managed tag: %s", e)
                return None

        return self._managed_tag_id

    async def _apply_managed_tag(self, endpoint: str, obj_id: str) -> bool:
        """Apply the managed tag to an object."""
        if self.dry_run:
            return False

        tag_id = await self._ensure_managed_tag()
        if not tag_id:
            return False

        try:
            obj = await self.client.get(f"{endpoint}{obj_id}/")
            current_tags = obj.get("tags", [])
            current_tag_ids = [t["id"] if isinstance(t, dict) else t for t in current_tags]
            if tag_id in current_tag_ids:
                return True

            new_tags = [*current_tag_ids, tag_id]
            await self.client.patch(f"{endpoint}{obj_id}/", {"tags": new_tags})
        except Exception as e:
            LOGGER.debug("Failed to apply managed tag to %s%s: %s", endpoint, obj_id, e)
            return False
        else:
            return True

    def _track_object(self, obj_type: str, obj_id: str) -> None:
        """Track an object as touched during this sync."""
        if obj_type in self._touched_objects:
            self._touched_objects[obj_type].add(obj_id)

    async def _get_location_for_hostname(self, hostname: str) -> dict[str, Any] | None:
        """Determine the appropriate location for a hostname based on location_mapping or default location_name."""
        target_location_name = self.location_name

        for prefix, location_name in self.location_mapping.items():
            if hostname.lower().startswith(prefix.lower()):
                target_location_name = location_name
                break

        if not target_location_name:
            return None

        return await self._get_or_create_location(target_location_name)

    async def _get_or_create_location(self, location_name: str) -> dict[str, Any] | None:
        """Get or create a location by name."""
        if location_name in self._location_cache:
            return self._location_cache[location_name]

        endpoints = self.mapping.get_nautobot_endpoints()
        existing = await self._find_nautobot_object(endpoints["locations"], name=location_name)

        if existing:
            self._location_cache[location_name] = existing
            return existing

        if self.create_prerequisites and not self.dry_run:
            # Get location type, status, and tag
            location_type_id = await self._ensure_location_type()
            status_id = await self._get_status_id("Active", "dcim.location")
            tag_id = await self._ensure_managed_tag()

            location_data: dict[str, Any] = {"name": location_name}
            if location_type_id:
                location_data["location_type"] = location_type_id
            if status_id:
                location_data["status"] = status_id
            if tag_id:
                location_data["tags"] = [tag_id]

            new_location = await self.client.post(endpoints["locations"], location_data)
            self._location_cache[location_name] = new_location
            LOGGER.info("Created location: %s", location_name)
            return new_location

        return None
