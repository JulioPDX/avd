# Copyright (c) 2023-2025 Arista Networks, Inc.
# Use of this source code is governed by the Apache License 2.0
# that can be found in the LICENSE file.
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from pyavd._eos_cli_config_gen.schema import EosCliConfigGen
from pyavd._eos_designs.structured_config.structured_config_generator import structured_config_contributor
from pyavd._utils import get

if TYPE_CHECKING:
    from . import AvdStructuredConfigNetworkServicesProtocol


class RouterPathSelectionMixin(Protocol):
    """
    Mixin Class used to generate structured config for one key.

    Class should only be used as Mixin to a AvdStructuredConfig class.
    """

    @structured_config_contributor
    def router_path_selection(self: AvdStructuredConfigNetworkServicesProtocol) -> None:
        """Set the structured config for router path-selection (DPS)."""
        if not self.shared_utils.is_wan_router:
            return

        self._set_wan_load_balance_policies()

        # When running CV Pathfinder, only load balance policies are configured
        # for AutoVPN, need also vrfs and policies.
        if self.inputs.wan_mode == "autovpn":
            for vrf in self._filtered_wan_vrfs:
                self.structured_config.router_path_selection.vrfs.append_new(
                    name=vrf.name,
                    path_selection_policy=f"{vrf.policy}-WITH-CP" if vrf.name == "default" else vrf.policy,
                )

            self._set_autovpn_policies()

    def _set_wan_load_balance_policies(self: AvdStructuredConfigNetworkServicesProtocol) -> None:
        """Set list of load balance policies."""
        for policy in self._filtered_wan_policies:
            for match in policy.get("matches", []):
                if "load_balance_policy" in match:
                    self.structured_config.router_path_selection.load_balance_policies.append(match["load_balance_policy"])

            if (default_match := policy.get("default_match")) is not None and "load_balance_policy" in default_match:
                self.structured_config.router_path_selection.load_balance_policies.append(default_match["load_balance_policy"])

    def _set_autovpn_policies(self: AvdStructuredConfigNetworkServicesProtocol) -> None:
        """Set list of policies for AutoVPN."""
        for policy in self._filtered_wan_policies:
            policy_item = EosCliConfigGen.RouterPathSelection.PoliciesItem(name=policy["name"])
            for index, match in enumerate(get(policy, "matches", default=[]), start=1):
                policy_item.rules.append_new(
                    id=10 * index,
                    application_profile=get(match, "application_profile"),
                    load_balance=match["load_balance_policy"].name if "load_balance_policy" in match else None,
                )
            if (default_match := policy.get("default_match")) is not None and "load_balance_policy" in default_match:
                policy_item.default_match.load_balance = default_match["load_balance_policy"].name
            self.structured_config.router_path_selection.policies.append(policy_item)
