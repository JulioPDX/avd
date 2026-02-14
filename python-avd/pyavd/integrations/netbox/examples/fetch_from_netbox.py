# Copyright (c) 2023-2026 Arista Networks, Inc.
# Use of this source code is governed by the Apache License 2.0
# that can be found in the LICENSE file.
# ruff: noqa: T201
"""
Example script: Fetch data from NetBox for AVD.

This script demonstrates how to fetch device inventory and network data
from NetBox to bootstrap or update an AVD deployment.

Usage:
    python fetch_from_netbox.py --netbox-url https://netbox.example.com \
        --token nbt_xxx.yyy --site DC1 --output inventory.yml
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import yaml

# Add parent path for development
sys.path.insert(0, str(Path(__file__).parents[4]))

from pyavd.integrations.netbox import AVDNetBoxSync, NetBoxClient


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch data from NetBox for AVD")
    parser.add_argument("--netbox-url", required=True, help="NetBox URL")
    parser.add_argument("--token", required=True, help="NetBox API token")
    parser.add_argument("--site", help="NetBox site filter")
    parser.add_argument("--output", "-o", default="inventory.yml", help="Output file")
    parser.add_argument("--fetch-vlans", action="store_true", help="Also fetch VLANs")
    parser.add_argument("--fetch-vrfs", action="store_true", help="Also fetch VRFs")
    parser.add_argument("--fetch-prefixes", action="store_true", help="Also fetch prefixes")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")

    args = parser.parse_args()

    # Setup logging
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    print(f"Connecting to NetBox: {args.netbox_url}")

    with NetBoxClient(args.netbox_url, args.token) as client:
        sync = AVDNetBoxSync(client)

        # Fetch device inventory
        print(f"Fetching devices from NetBox{f' (site: {args.site})' if args.site else ''}...")
        inventory = sync.generate_avd_inventory(args.site)

        # Count devices
        device_count = sum(len(group.get("hosts", {})) for group in inventory.get("all", {}).get("children", {}).get("FABRIC", {}).get("children", {}).values())
        print(f"Found {device_count} devices")

        # Fetch additional data if requested
        extra_data = {}

        if args.fetch_vlans:
            print("Fetching VLANs...")
            vlans = sync.fetch_vlans_from_netbox(args.site)
            extra_data["vlans"] = vlans
            print(f"Found {len(vlans)} VLANs")

        if args.fetch_vrfs:
            print("Fetching VRFs...")
            vrfs = sync.fetch_vrfs_from_netbox()
            extra_data["vrfs"] = vrfs
            print(f"Found {len(vrfs)} VRFs")

        if args.fetch_prefixes:
            print("Fetching prefixes...")
            prefixes = sync.fetch_prefixes_from_netbox()
            extra_data["prefixes"] = prefixes
            print(f"Found {len(prefixes)} prefixes")

        # Combine inventory with extra data
        output_data = {
            "inventory": inventory,
            **extra_data,
        }

        # Write output
        output_path = Path(args.output)
        with output_path.open("w") as f:
            yaml.dump(output_data, f, default_flow_style=False, sort_keys=False)

        print(f"\nOutput written to: {output_path}")


if __name__ == "__main__":
    main()
