# Copyright (c) 2023-2026 Arista Networks, Inc.
# Use of this source code is governed by the Apache License 2.0
# that can be found in the LICENSE file.
"""Async helper methods for AVD to NetBox synchronization."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import httpx
import yaml

from .models import NODE_TYPE_TO_DEVICE_ROLE, AVDNetBoxMapping
from .sync import DEFAULT_DEVICE_TYPE, DEFAULT_MANUFACTURER

if TYPE_CHECKING:
    import asyncio
from .transforms import get_nested_value, slugify

if TYPE_CHECKING:
    from .client import AsyncNetBoxClient

LOGGER = logging.getLogger(__name__)


class AsyncHelpersMixin:
    """Mixin class providing async helper methods for NetBox sync."""

    client: AsyncNetBoxClient
    mapping: AVDNetBoxMapping
    dry_run: bool
    create_prerequisites: bool
    managed_tag: str
    site_name: str | None
    site_mapping: dict[str, str]
    devicetype_library_url: str | None
    platform_mapping: dict[str, str]
    _cache: dict[str, dict[str, Any]]
    _cache_lock: asyncio.Lock
    _site_cache: dict[str, dict[str, Any]]
    _managed_tag_id: int | None
    _prerequisites_created: bool
    _prerequisite_lock: asyncio.Lock
    _devicetype_library_cache: dict[str, dict[str, Any] | None]
    _library_lock: asyncio.Lock
    _touched_objects: dict[str, set[int]]

    def _get_library_model_name(self, platform_name: str) -> str:
        """Get the device type model name to use for library lookup."""
        return self.platform_mapping.get(platform_name, platform_name)

    async def _fetch_devicetype_from_library(self, model_name: str) -> dict[str, Any] | None:
        """Fetch device type definition from the NetBox Community Device Type Library."""
        if not self.devicetype_library_url:
            return None

        # Check cache first (with lock to prevent race conditions)
        async with self._library_lock:
            if model_name in self._devicetype_library_cache:
                return self._devicetype_library_cache[model_name]

        # Try different filename variations
        filenames_to_try = [f"{model_name}.yaml", f"{model_name}.yml"]

        async with httpx.AsyncClient(timeout=10.0) as http_client:
            for filename in filenames_to_try:
                url = f"{self.devicetype_library_url.rstrip('/')}/{filename}"
                try:
                    LOGGER.debug("Fetching device type from library: %s", url)
                    response = await http_client.get(url)
                    if response.status_code == 200:
                        device_type_def = yaml.safe_load(response.text)
                        async with self._library_lock:
                            self._devicetype_library_cache[model_name] = device_type_def
                        LOGGER.info("Found device type in library: %s", model_name)
                        return device_type_def
                except Exception as e:
                    LOGGER.debug("Failed to fetch device type '%s' from library: %s", model_name, e)
                    continue

        # Not found - cache the miss
        async with self._library_lock:
            self._devicetype_library_cache[model_name] = None
        return None

    async def _create_devicetype_from_library(self, library_def: dict[str, Any], manufacturer_id: int) -> dict[str, Any] | None:
        """Create a device type in NetBox using a definition from the library."""
        endpoints = self.mapping.get_netbox_endpoints()

        device_type_data = {
            "manufacturer": manufacturer_id,
            "model": library_def.get("model"),
            "slug": library_def.get("slug", slugify(library_def.get("model", ""))),
        }

        # Add optional physical specs
        for field in ["part_number", "u_height", "is_full_depth", "airflow", "comments"]:
            if field in library_def:
                device_type_data[field] = library_def[field]
        if "weight" in library_def:
            device_type_data["weight"] = library_def["weight"]
            if "weight_unit" in library_def:
                device_type_data["weight_unit"] = library_def["weight_unit"]

        try:
            device_type = await self.client.post(endpoints["device_types"], device_type_data)
            LOGGER.info("Created device type from library: %s", library_def.get("model"))
        except Exception as e:
            LOGGER.warning("Failed to create device type from library: %s", e)
            return None
        else:
            return device_type

    async def _get_or_cache(self, cache_key: str, endpoint: str, lookup_field: str) -> dict[str, Any]:
        """Get cached lookup table or fetch from NetBox."""
        async with self._cache_lock:
            if cache_key not in self._cache:
                self._cache[cache_key] = {}
                async for obj in self.client.get_all(endpoint):
                    key = get_nested_value(obj, lookup_field)
                    if key:
                        self._cache[cache_key][key] = obj
        return self._cache[cache_key]

    async def _find_netbox_object(self, endpoint: str, **kwargs: Any) -> dict[str, Any] | None:
        """Find a single object in NetBox by query parameters."""
        response = await self.client.get(endpoint, params=kwargs)
        results = response.get("results", [])
        return results[0] if results else None

    async def _ensure_prerequisites(self) -> None:
        """Ensure required objects exist in NetBox (manufacturer, device type, roles)."""
        if self._prerequisites_created or not self.create_prerequisites:
            return

        endpoints = self.mapping.get_netbox_endpoints()

        # Create manufacturer if needed
        existing = await self._find_netbox_object(endpoints["manufacturers"], slug=DEFAULT_MANUFACTURER["slug"])
        if not existing and not self.dry_run:
            await self.client.post(endpoints["manufacturers"], DEFAULT_MANUFACTURER)
            LOGGER.info("Created manufacturer: %s", DEFAULT_MANUFACTURER["name"])

        # Create device type if needed
        existing = await self._find_netbox_object(endpoints["device_types"], slug=DEFAULT_DEVICE_TYPE["slug"])
        if not existing and not self.dry_run:
            mfr = await self._find_netbox_object(endpoints["manufacturers"], slug=DEFAULT_MANUFACTURER["slug"])
            if mfr:
                device_type_data = {**DEFAULT_DEVICE_TYPE, "manufacturer": mfr["id"]}
                await self.client.post(endpoints["device_types"], device_type_data)
                LOGGER.info("Created device type: %s", DEFAULT_DEVICE_TYPE["model"])

        # Create device roles
        for role_name in NODE_TYPE_TO_DEVICE_ROLE.values():
            role_slug = slugify(role_name)
            existing = await self._find_netbox_object(endpoints["device_roles"], slug=role_slug)
            if not existing and not self.dry_run:
                await self.client.post(endpoints["device_roles"], {"name": role_name, "slug": role_slug})
                LOGGER.info("Created device role: %s", role_name)

        self._prerequisites_created = True

    async def _ensure_managed_tag(self) -> int | None:
        """Ensure the managed tag exists and return its ID."""
        if self._managed_tag_id:
            return self._managed_tag_id

        endpoints = self.mapping.get_netbox_endpoints()
        tag_slug = slugify(self.managed_tag)

        existing = await self._find_netbox_object(endpoints["tags"], slug=tag_slug)
        if existing:
            self._managed_tag_id = existing["id"]
            return self._managed_tag_id

        try:
            tag_data = {
                "name": self.managed_tag,
                "slug": tag_slug,
                "color": "0077ff",
                "description": "Objects managed by AVD NetBox sync - do not modify manually",
            }
            tag = await self.client.post(endpoints["tags"], tag_data)
            self._managed_tag_id = tag["id"]
            LOGGER.info("Created managed tag: %s", self.managed_tag)
        except Exception as e:
            LOGGER.warning("Failed to create managed tag: %s", e)
            return None
        else:
            return self._managed_tag_id

    async def _apply_managed_tag(self, endpoint: str, obj_id: int) -> bool:
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

    def _track_object(self, obj_type: str, obj_id: int) -> None:
        """Track an object as touched during this sync."""
        if obj_type in self._touched_objects:
            self._touched_objects[obj_type].add(obj_id)

    async def _get_site_for_hostname(self, hostname: str) -> dict[str, Any] | None:
        """Determine the appropriate site for a hostname based on site_mapping or default site_name."""
        target_site_name = self.site_name

        for prefix, site_name in self.site_mapping.items():
            if hostname.lower().startswith(prefix.lower()):
                target_site_name = site_name
                break

        if not target_site_name:
            return None

        return await self._get_or_create_site(target_site_name)

    async def _get_or_create_site(self, site_name: str) -> dict[str, Any] | None:
        """Get or create a site by name."""
        if site_name in self._site_cache:
            return self._site_cache[site_name]

        endpoints = self.mapping.get_netbox_endpoints()
        existing = await self._find_netbox_object(endpoints["sites"], name=site_name)

        if existing:
            self._site_cache[site_name] = existing
            return existing

        if self.create_prerequisites and not self.dry_run:
            site_data = {"name": site_name, "slug": slugify(site_name)}
            new_site = await self.client.post(endpoints["sites"], site_data)
            self._site_cache[site_name] = new_site
            LOGGER.info("Created site: %s", site_name)
            return new_site

        return None
