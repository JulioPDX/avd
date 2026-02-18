# Copyright (c) 2026 Arista Networks, Inc.
# Use of this source code is governed by the Apache License 2.0
# that can be found in the LICENSE file.
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ansible.errors import AnsibleActionFail
from ansible.plugins.action import ActionBase
from yaml import load

from ansible_collections.arista.avd.plugins.plugin_utils.utils import YamlLoader

if TYPE_CHECKING:
    from pyavd.integrations.nautobot import AsyncAVDNautobotSync, AsyncNautobotClient

PLUGIN_NAME = "arista.avd.nautobot_deploy"

try:
    from pyavd._utils import strip_empties_from_dict
    from pyavd.integrations.nautobot import AsyncAVDNautobotSync, AsyncNautobotClient

    HAS_PYAVD = True
except ImportError:
    HAS_PYAVD = False

try:
    import httpx

    HAS_HTTPX = True
    del httpx
except ImportError:
    HAS_HTTPX = False


LOGGER = logging.getLogger("ansible_collections.arista.avd")

ARGUMENT_SPEC = {
    "nautobot_url": {"type": "str", "required": True},
    "nautobot_token": {"type": "str", "secret": True, "required": True},
    "location_name": {"type": "str", "required": False},
    "location_mapping": {"type": "dict", "required": False},
    "structured_config_dir": {"type": "str", "required": False},  # Not required when purge=True
    "structured_config_suffix": {"type": "str", "default": "yml"},
    "device_list": {"type": "list", "elements": "str", "required": False},
    "node_type_mapping": {"type": "dict", "required": False},
    "verify_ssl": {"type": "bool", "default": True},
    "timeout": {"type": "float", "default": 30.0},
    "create_prerequisites": {"type": "bool", "default": True},
    "dry_run": {"type": "bool", "default": False},
    "return_details": {"type": "bool", "default": False},
    "fail_on_errors": {"type": "bool", "default": False},
    "reconcile": {"type": "bool", "default": False},
    "managed_tag": {"type": "str", "default": "avd-managed"},
    "max_concurrent": {"type": "int", "default": 10},
    "purge": {"type": "bool", "default": False},
    "purge_prerequisites": {"type": "bool", "default": False},
}


def setup_module_logging(result: dict) -> None:
    """Set up Ansible logging for the module."""
    python_to_ansible_handler = PythonToAnsibleHandler(result, display)
    LOGGER.addHandler(python_to_ansible_handler)
    LOGGER.setLevel(logging.DEBUG)


try:
    from ansible.plugins.action import display

    from ansible_collections.arista.avd.plugins.plugin_utils.utils import PythonToAnsibleHandler
except ImportError:
    pass


class ActionModule(ActionBase):
    """Ansible action plugin to sync AVD structured configs to Nautobot."""

    def run(self, tmp: Any = None, task_vars: dict | None = None) -> dict:
        """Execute the action plugin."""
        self._supports_check_mode = True

        if task_vars is None:
            task_vars = {}

        result = super().run(tmp, task_vars)
        del tmp  # tmp no longer has any effect

        if not HAS_PYAVD:
            msg = f"The '{PLUGIN_NAME}' plugin requires the 'pyavd' Python library. Got import error"
            raise AnsibleActionFail(msg)

        if not HAS_HTTPX:
            msg = f"The '{PLUGIN_NAME}' plugin requires the 'httpx' Python library. Install with: pip install httpx"
            raise AnsibleActionFail(msg)

        # Set up logging
        setup_module_logging(result)

        # Get task arguments and validate them
        _validation_result, validated_args = self.validate_argument_spec(ARGUMENT_SPEC)
        validated_args = strip_empties_from_dict(validated_args)

        # Converting to json and back to remove any AnsibleUnsafe types
        validated_args = json.loads(json.dumps(validated_args))

        # Log args without secrets
        logged_args = validated_args.copy()
        if "nautobot_token" in logged_args:
            logged_args["nautobot_token"] = "<removed>"  # noqa: S105
        LOGGER.info("nautobot_deploy: %s", logged_args)

        # Run the sync
        return self.deploy(validated_args, result)

    def deploy(self, validated_args: dict, result: dict) -> dict:
        """Load configs and perform Nautobot sync or purge."""
        purge_mode = validated_args.get("purge", False)

        # Purge mode: delete all AVD-managed objects, skip sync
        if purge_mode:
            return self._run_purge(validated_args, result)

        # Normal sync mode: validate location_name or location_mapping is provided
        location_name = validated_args.get("location_name")
        location_mapping = validated_args.get("location_mapping")
        if not location_name and not location_mapping:
            msg = "Either 'location_name' or 'location_mapping' must be provided"
            raise AnsibleActionFail(msg)

        # Validate structured_config_dir is provided for sync mode
        if not validated_args.get("structured_config_dir"):
            msg = "'structured_config_dir' is required when purge=False"
            raise AnsibleActionFail(msg)

        try:
            # Load structured configs
            configs, node_types = self._load_structured_configs(
                structured_config_dir=validated_args["structured_config_dir"],
                structured_config_suffix=validated_args.get("structured_config_suffix", "yml"),
                device_list=validated_args.get("device_list"),
                node_type_mapping=validated_args.get("node_type_mapping"),
            )

            if not configs:
                result["changed"] = False
                result["msg"] = "No structured configs found to sync"
                return result

            # Check for dry_run / check_mode
            dry_run = validated_args.get("dry_run", False) or self._play_context.check_mode

            # Connect to Nautobot and sync
            sync_result = self._run_async_sync(validated_args, configs, node_types, location_name, location_mapping, dry_run)

            # Populate result
            result["changed"] = sync_result.created > 0 or sync_result.updated > 0 or sync_result.deleted > 0
            result["created"] = sync_result.created
            result["updated"] = sync_result.updated
            result["skipped"] = sync_result.skipped
            result["deleted"] = sync_result.deleted
            result["errors"] = sync_result.errors

            # Only fail if fail_on_errors is True (default: False)
            fail_on_errors = validated_args.get("fail_on_errors", False)
            result["failed"] = fail_on_errors and len(sync_result.errors) > 0

            if validated_args.get("return_details"):
                result["devices"] = list(configs.keys())
                result["node_types"] = node_types
                result["dry_run"] = dry_run

            if sync_result.errors:
                result["msg"] = f"Sync completed with {len(sync_result.errors)} error(s)"
            else:
                msg_parts = [f"{sync_result.created} created", f"{sync_result.updated} updated", f"{sync_result.skipped} skipped"]
                if sync_result.deleted > 0:
                    msg_parts.append(f"{sync_result.deleted} deleted")
                result["msg"] = f"Sync completed: {', '.join(msg_parts)}"

        except Exception as e:
            LOGGER.exception("Nautobot sync failed")
            result["failed"] = True
            result["msg"] = f"Nautobot sync failed: {e}"

        return result

    def _run_purge(self, validated_args: dict, result: dict) -> dict:
        """Delete all AVD-managed objects from Nautobot."""
        dry_run = validated_args.get("dry_run", False) or self._play_context.check_mode

        try:
            purge_result = self._run_async_purge(validated_args, dry_run)

            result["changed"] = purge_result.deleted > 0
            result["created"] = 0
            result["updated"] = 0
            result["skipped"] = purge_result.skipped
            result["deleted"] = purge_result.deleted
            result["errors"] = purge_result.errors

            fail_on_errors = validated_args.get("fail_on_errors", False)
            result["failed"] = fail_on_errors and len(purge_result.errors) > 0

            if validated_args.get("return_details"):
                result["dry_run"] = dry_run
                result["purge"] = True

            if purge_result.errors:
                result["msg"] = f"Purge completed with {len(purge_result.errors)} error(s)"
            elif dry_run:
                result["msg"] = f"Purge dry run: {purge_result.skipped} objects would be deleted"
            else:
                result["msg"] = f"Purge completed: {purge_result.deleted} objects deleted"

        except Exception as e:
            LOGGER.exception("Nautobot purge failed")
            result["failed"] = True
            result["msg"] = f"Nautobot purge failed: {e}"

        return result

    def _run_async_purge(self, validated_args: dict, dry_run: bool) -> Any:
        """Run async purge using asyncio.run()."""

        async def _async_purge() -> Any:
            async with AsyncNautobotClient(
                url=validated_args["nautobot_url"],
                token=validated_args["nautobot_token"],
                verify_ssl=validated_args.get("verify_ssl", True),
                timeout=validated_args.get("timeout", 30.0),
                max_concurrent=validated_args.get("max_concurrent", 10),
            ) as client:
                sync = AsyncAVDNautobotSync(
                    client=client,
                    dry_run=dry_run,
                    managed_tag=validated_args.get("managed_tag"),
                    max_concurrent=validated_args.get("max_concurrent", 10),
                )
                return await sync.purge_all(purge_prerequisites=validated_args.get("purge_prerequisites", False))

        return asyncio.run(_async_purge())

    def _run_async_sync(
        self,
        validated_args: dict,
        configs: dict,
        node_types: dict,
        location_name: str | None,
        location_mapping: dict | None,
        dry_run: bool,
    ) -> Any:
        """Run async sync using asyncio.run()."""

        async def _async_sync() -> Any:
            async with AsyncNautobotClient(
                url=validated_args["nautobot_url"],
                token=validated_args["nautobot_token"],
                verify_ssl=validated_args.get("verify_ssl", True),
                timeout=validated_args.get("timeout", 30.0),
                max_concurrent=validated_args.get("max_concurrent", 10),
            ) as client:
                sync = AsyncAVDNautobotSync(
                    client=client,
                    location_name=location_name,
                    location_mapping=location_mapping,
                    dry_run=dry_run,
                    create_prerequisites=validated_args.get("create_prerequisites", True),
                    reconcile=validated_args.get("reconcile", False),
                    managed_tag=validated_args.get("managed_tag"),
                    max_concurrent=validated_args.get("max_concurrent", 10),
                )
                return await sync.sync_all(configs, node_types)

        return asyncio.run(_async_sync())

    def _load_structured_configs(
        self,
        structured_config_dir: str,
        structured_config_suffix: str,
        device_list: list[str] | None,
        node_type_mapping: dict[str, str] | None,
    ) -> tuple[dict[str, dict], dict[str, str]]:
        """
        Load AVD structured configs from files.

        Args:
            structured_config_dir: Path to directory containing structured config files.
            structured_config_suffix: File suffix for config files (yml, yaml, json).
            device_list: Optional list of specific devices to load.
            node_type_mapping: Optional explicit mapping of hostname to node type.

        Returns:
            Tuple of (configs dict, node_types dict).
        """
        configs: dict[str, dict] = {}
        node_types: dict[str, str] = {}

        config_path = Path(structured_config_dir)
        if not config_path.exists():
            LOGGER.warning("Structured config directory does not exist: %s", structured_config_dir)
            return configs, node_types

        # Find all config files
        pattern = f"*.{structured_config_suffix}"
        for config_file in config_path.glob(pattern):
            hostname = config_file.stem

            # Skip CVP files
            if hostname.lower().startswith("cvp"):
                continue

            # Filter by device_list if provided
            if device_list and hostname not in device_list:
                continue

            # Load the config
            # YamlLoader is the standard AVD loader that handles Ansible-specific YAML features
            with config_file.open() as f:
                config = load(f, Loader=YamlLoader)  # noqa: S506

            if config:
                # Use hostname from config if available
                actual_hostname = config.get("hostname", hostname)
                configs[actual_hostname] = config

                # Determine node type
                if node_type_mapping and actual_hostname in node_type_mapping:
                    node_types[actual_hostname] = node_type_mapping[actual_hostname]
                else:
                    node_types[actual_hostname] = self._infer_node_type(actual_hostname, config)

        LOGGER.info("Loaded %d structured configs from %s", len(configs), structured_config_dir)
        return configs, node_types

    def _infer_node_type(self, hostname: str, config: dict) -> str:
        """
        Infer node type from hostname pattern or config.

        Args:
            hostname: Device hostname.
            config: Device structured config.

        Returns:
            Inferred node type string.
        """
        hostname_lower = hostname.lower()

        # Check for explicit type markers in config metadata
        if "metadata" in config:
            metadata = config["metadata"]
            if "cv_tags" in metadata:
                for tag in metadata.get("cv_tags", {}).get("device_tags", []):
                    if tag.get("name") == "topology_type":
                        return tag.get("value", "unknown")

        # Infer from hostname patterns using a priority-based check
        return self._match_hostname_pattern(hostname_lower)

    def _match_hostname_pattern(self, hostname_lower: str) -> str:
        """
        Match hostname to node type pattern.

        Args:
            hostname_lower: Lowercase hostname.

        Returns:
            Node type string.
        """
        # Spine types
        if "spine" in hostname_lower:
            if "l2spine" in hostname_lower or "l2-spine" in hostname_lower:
                return "l2spine"
            if "l3spine" in hostname_lower or "l3-spine" in hostname_lower:
                return "l3spine"
            return "spine"

        # Leaf types
        if "leaf" in hostname_lower:
            if hostname_lower.endswith("c") or "l2leaf" in hostname_lower or "l2-leaf" in hostname_lower:
                return "l2leaf"
            return "l3leaf"

        # MPLS node types
        if hostname_lower.startswith("p") and not hostname_lower.startswith("pe"):
            return "p"
        if hostname_lower.startswith("pe"):
            return "pe"
        if "rr" in hostname_lower and "wan" not in hostname_lower:
            return "rr"

        # WAN node types
        if "wan" in hostname_lower:
            return "wan_rr" if "rr" in hostname_lower else "wan_router"

        # Default
        return "l3leaf"
