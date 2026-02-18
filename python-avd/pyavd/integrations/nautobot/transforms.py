# Copyright (c) 2023-2026 Arista Networks, Inc.
# Use of this source code is governed by the Apache License 2.0
# that can be found in the LICENSE file.
"""Field transformation functions for AVD to Nautobot data conversion."""

from __future__ import annotations

import re
from typing import Any

from .models import INTERFACE_MODE_MAP, VLAN_STATUS_MAP


def slugify(value: str) -> str:
    """Convert a string to a Nautobot-compatible slug."""
    if not value:
        return ""
    # Convert to lowercase, replace spaces/underscores with hyphens
    slug = value.lower().strip()
    slug = re.sub(r"[\s_]+", "-", slug)
    # Remove any characters that aren't alphanumeric or hyphens
    slug = re.sub(r"[^a-z0-9-]", "", slug)
    # Remove consecutive hyphens
    slug = re.sub(r"-+", "-", slug)
    return slug.strip("-")


def invert_bool(value: bool | None) -> bool | None:
    """Invert a boolean value (AVD shutdown=True -> Nautobot enabled=False)."""
    if value is None:
        return None
    return not value


def map_interface_mode(mode: str | None) -> str | None:
    """Map AVD interface mode to Nautobot interface mode."""
    if not mode:
        return None
    return INTERFACE_MODE_MAP.get(mode.lower(), mode)


def map_vlan_status(state: str | None) -> str:
    """Map AVD VLAN state to Nautobot VLAN status name."""
    if not state:
        return "Active"
    return VLAN_STATUS_MAP.get(state.lower(), "Active")


def parse_speed(speed: str | None) -> int | None:
    """
    Parse AVD speed string to Nautobot speed in kbps.

    Examples:
        "10g" -> 10000000
        "100g" -> 100000000
        "25g" -> 25000000
        "1g" -> 1000000
        "100m" -> 100000
        "forced 10g" -> 10000000
    """
    if not speed:
        return None

    # Remove "forced" prefix if present
    speed = speed.lower().replace("forced", "").strip()

    # Match patterns like "10g", "100m", "25gbase-cr"
    match = re.match(r"(\d+)([gm])", speed)
    if not match:
        return None

    value = int(match.group(1))
    unit = match.group(2)

    if unit == "g":
        return value * 1000000  # Gbps to kbps
    if unit == "m":
        return value * 1000  # Mbps to kbps

    return None


def map_interface_type(intf_name: str) -> str:
    """
    Map interface name to Nautobot interface type.

    Based on interface naming conventions:
        Ethernet* -> 1000base-t (default, can be overridden)
        Management* -> 1000base-t
        Loopback* -> virtual
        Vlan* -> virtual
        Port-Channel* -> lag
        Vxlan* -> virtual
    """
    if not intf_name:
        return "other"

    name_lower = intf_name.lower()

    if name_lower.startswith(("loopback", "vlan", "vxlan")):
        return "virtual"
    if name_lower.startswith("port-channel"):
        return "lag"
    if name_lower.startswith("management"):
        return "1000base-t"
    if name_lower.startswith("ethernet"):
        return "other"  # Actual type determined by transceiver

    return "other"


def get_nested_value(data: dict[str, Any], path: str) -> Any:
    """
    Get a value from a nested dictionary using dot notation.

    Args:
        data: The dictionary to search
        path: Dot-separated path (e.g., "metadata.platform")

    Returns:
        The value at the path, or None if not found
    """
    keys = path.split(".")
    current = data

    for key in keys:
        if isinstance(current, dict) and key in current:
            current = current[key]
        else:
            return None

    return current


def set_nested_value(data: dict[str, Any], path: str, value: Any) -> None:
    """
    Set a value in a nested dictionary using dot notation.

    Args:
        data: The dictionary to modify
        path: Dot-separated path (e.g., "custom_fields.system_mac")
        value: The value to set
    """
    keys = path.split(".")
    current = data

    for key in keys[:-1]:
        if key not in current:
            current[key] = {}
        current = current[key]

    current[keys[-1]] = value


# Registry of available transform functions
TRANSFORM_REGISTRY: dict[str, Any] = {
    "slugify": slugify,
    "invert_bool": invert_bool,
    "map_interface_mode": map_interface_mode,
    "map_vlan_status": map_vlan_status,
    "parse_speed": parse_speed,
    "map_interface_type": map_interface_type,
}


def apply_transform(transform_name: str, value: Any) -> Any:
    """Apply a named transform function to a value."""
    if transform_name not in TRANSFORM_REGISTRY:
        msg = f"Unknown transform: {transform_name}"
        raise ValueError(msg)
    return TRANSFORM_REGISTRY[transform_name](value)
