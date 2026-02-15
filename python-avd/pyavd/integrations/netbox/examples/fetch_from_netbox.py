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
        --token nbt_xxx.yyy --site dc1 --output inventory.yml

    # Generate complete AVD group_vars from NetBox
    python fetch_from_netbox.py --netbox-url https://netbox.example.com \
        --token nbt_xxx.yyy --site dc1 --generate-group-vars --output group_vars.yml
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
    parser.add_argument("--site", help="NetBox site filter (use slug, e.g., 'dc1')")
    parser.add_argument("--output", "-o", default="inventory.yml", help="Output file")
    parser.add_argument("--fetch-vlans", action="store_true", help="Also fetch VLANs")
    parser.add_argument("--fetch-vrfs", action="store_true", help="Also fetch VRFs")
    parser.add_argument("--fetch-prefixes", action="store_true", help="Also fetch prefixes")
    parser.add_argument("--fetch-cables", action="store_true", help="Also fetch cable connections")
    parser.add_argument("--fetch-ips", action="store_true", help="Also fetch IP addresses")
    parser.add_argument(
        "--generate-group-vars",
        action="store_true",
        help="Generate complete AVD group_vars (includes topology, VLANs, VRFs)",
    )
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

        # Generate complete group_vars if requested
        if args.generate_group_vars:
            print(f"Generating AVD group_vars from NetBox{f' (site: {args.site})' if args.site else ''}...")
            group_vars = sync.generate_avd_group_vars(args.site)

            # Count items
            spines_data = group_vars.get("SPINES", {})
            l3leafs_data = group_vars.get("L3_LEAFS", {})
            l2leafs_data = group_vars.get("L2_LEAFS") or {}
            network_services = group_vars.get("NETWORK_SERVICES", {})

            spine_count = len(spines_data.get("spine", {}).get("nodes", []))
            l3leaf_groups = len(l3leafs_data.get("l3leaf", {}).get("node_groups", []))
            l2leaf_groups = len(l2leafs_data.get("l2leaf", {}).get("node_groups", [])) if l2leafs_data else 0
            tenants = len(network_services.get("tenants", []))

            print(f"Generated group_vars: {spine_count} spines, {l3leaf_groups} L3 leaf groups, {l2leaf_groups} L2 leaf groups, {tenants} tenants")

            # Write output files
            output_dir = Path(args.output).parent
            output_dir.mkdir(parents=True, exist_ok=True)

            # Write each group_vars file
            files_written = []
            for group_name, data in group_vars.items():
                if data is None:
                    continue
                output_path = output_dir / f"{group_name}.yml"
                with output_path.open("w") as f:
                    f.write("---\n")
                    yaml.dump(data, f, default_flow_style=False, sort_keys=False)
                files_written.append(str(output_path))

            print("\nGroup vars written to:")
            for fpath in files_written:
                print(f"  - {fpath}")
            return

        # Fetch device inventory
        print(f"Fetching devices from NetBox{f' (site: {args.site})' if args.site else ''}...")
        inventory = sync.generate_avd_inventory(args.site)

        # Count devices
        fabric_children = inventory.get("all", {}).get("children", {}).get("FABRIC", {}).get("children", {})
        device_count = sum(len(group.get("hosts", {})) for group in fabric_children.values())
        print(f"Found {device_count} devices")

        # Fetch additional data if requested
        extra_data: dict = {}

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

        if args.fetch_cables:
            print("Fetching cables...")
            cables = sync.fetch_cables_from_netbox(args.site)
            extra_data["cables"] = cables
            print(f"Found {len(cables)} cable connections")

        if args.fetch_ips:
            print("Fetching IP addresses...")
            ips = sync.fetch_ip_addresses_from_netbox(args.site)
            extra_data["ip_addresses"] = ips
            print(f"Found {len(ips)} IP addresses")

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
