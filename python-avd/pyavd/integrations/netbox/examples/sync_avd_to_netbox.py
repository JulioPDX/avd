# Copyright (c) 2023-2026 Arista Networks, Inc.
# Use of this source code is governed by the Apache License 2.0
# that can be found in the LICENSE file.
# ruff: noqa: T201
"""
Example script: Sync AVD structured configs to NetBox.

This script demonstrates how to sync AVD-generated structured configurations
to NetBox for network documentation purposes.

Usage:
    python sync_avd_to_netbox.py --netbox-url https://netbox.example.com \
        --token nbt_xxx.yyy --site DC1 --configs-dir ./intended/structured_configs
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


def load_structured_configs(configs_dir: Path) -> tuple[dict, dict]:
    """
    Load AVD structured configs from a directory.

    Returns:
        Tuple of (configs dict, node_types dict)
    """
    configs = {}
    node_types = {}

    for yml_file in configs_dir.glob("*.yml"):
        if yml_file.name.startswith("cvp"):
            continue

        with yml_file.open() as f:
            config = yaml.safe_load(f)

        hostname = config.get("hostname", yml_file.stem)
        configs[hostname] = config

        # Try to infer node type from metadata
        # Node type might be in fabric_name or other metadata
        # For now, infer from hostname patterns
        if "spine" in hostname.lower():
            node_types[hostname] = "spine"
        elif "leaf" in hostname.lower():
            if "l2" in hostname.lower() or hostname.endswith("c"):
                node_types[hostname] = "l2leaf"
            else:
                node_types[hostname] = "l3leaf"

    return configs, node_types


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync AVD configs to NetBox")
    parser.add_argument("--netbox-url", required=True, help="NetBox URL")
    parser.add_argument("--token", required=True, help="NetBox API token")
    parser.add_argument("--site", required=True, help="NetBox site name")
    parser.add_argument("--configs-dir", required=True, help="Path to structured_configs directory")
    parser.add_argument("--dry-run", action="store_true", help="Don't make changes")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument("--sync-cables", action="store_true", help="Also sync cable connections")

    args = parser.parse_args()

    # Setup logging
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    configs_path = Path(args.configs_dir)
    if not configs_path.exists():
        print(f"Error: Configs directory not found: {configs_path}")
        sys.exit(1)

    # Load configs
    print(f"Loading structured configs from: {configs_path}")
    configs, node_types = load_structured_configs(configs_path)
    print(f"Found {len(configs)} device configurations")

    if not configs:
        print("No configurations found!")
        sys.exit(1)

    # Connect to NetBox and sync
    print(f"Connecting to NetBox: {args.netbox_url}")

    with NetBoxClient(args.netbox_url, args.token) as client:
        sync = AVDNetBoxSync(
            client,
            site_name=args.site,
            dry_run=args.dry_run,
            create_prerequisites=True,
        )

        print("Starting sync...")
        result = sync.sync_all(configs, node_types)

        print("\n" + "=" * 50)
        print("Sync Results:")
        print(f"  Created: {result.created}")
        print(f"  Updated: {result.updated}")
        print(f"  Skipped: {result.skipped}")
        print(f"  Errors:  {len(result.errors)}")

        if result.errors:
            print("\nErrors:")
            for error in result.errors:
                print(f"  - {error}")


if __name__ == "__main__":
    main()
