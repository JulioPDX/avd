# Copyright (c) 2023-2025 Arista Networks, Inc.
# Use of this source code is governed by the Apache License 2.0
# that can be found in the LICENSE file.
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from pyavd._eos_cli_config_gen.schema import EosCliConfigGen
from pyavd._eos_designs.structured_config.structured_config_generator import structured_config_contributor
from pyavd._utils import get, get_item

if TYPE_CHECKING:
    from . import AvdStructuredConfigNetworkServicesProtocol


class RouterAdaptiveVirtualTopologyMixin(Protocol):
    """
    Mixin Class used to generate structured config for one key.

    Class should only be used as Mixin to a AvdStructuredConfig class.
    """

    @structured_config_contributor
    def router_adaptive_virtual_topology(self: AvdStructuredConfigNetworkServicesProtocol) -> None:
        """Set the structured config for profiles, policies and VRFs for router adaptive-virtual-topology (AVT)."""
        if not self.shared_utils.is_cv_pathfinder_router:
            return

        self._set_cv_pathfinder_profiles()
        self._set_cv_pathfinder_wan_vrfs()
        self._set_cv_pathfinder_policies()

    def _set_cv_pathfinder_wan_vrfs(self: AvdStructuredConfigNetworkServicesProtocol) -> None:
        """Set the WAN VRFs based on filtered tenants and the AVT."""
        # For CV Pathfinder, it is required to go through all the AVT profiles in the policy to assign an ID.

        for vrf in self._filtered_wan_vrfs:
            wan_vrf = EosCliConfigGen.RouterAdaptiveVirtualTopology.VrfsItem(
                name=vrf.name, policy=f"{vrf.policy}-WITH-CP" if vrf.name == "default" else vrf.policy
            )

            # Need to allocate an ID for each profile in the policy, for now picked up from the input.
            policy = get_item(
                self._filtered_wan_policies,
                "name",
                wan_vrf.policy,
                required=True,
                custom_error_msg=(f"The policy {wan_vrf.policy} used in vrf {wan_vrf.name} is not configured under 'wan_virtual_topologies.policies'."),
            )

            for match in policy.get("matches", []):
                wan_vrf.profiles.append_new(
                    name=get(match, "avt_profile", required=True),
                    id=get(match, "id", required=True),
                )
            if (default_match := policy.get("default_match")) is not None:
                wan_vrf.profiles.append_new(
                    name=get(default_match, "avt_profile", required=True),
                    id=get(default_match, "id", required=True),
                )
            self.structured_config.router_adaptive_virtual_topology.vrfs.append(wan_vrf)

    def _set_cv_pathfinder_policies(self: AvdStructuredConfigNetworkServicesProtocol) -> None:
        """
        Set the CV Pathfinder policies based on the computed _filtered_wan_policies.

        It loops though the different match statements to build the appropriate entries
        by popping the load_balance_policy and id keys.
        """
        for policy in self._filtered_wan_policies:
            pathfinder_policy = EosCliConfigGen.RouterAdaptiveVirtualTopology.PoliciesItem(name=policy["name"])
            for match in get(policy, "matches", default=[]):
                # popping id, load_balance_and internet-exit policy
                pathfinder_policy.matches.append_new(
                    application_profile=match.get("application_profile"),
                    avt_profile=match.get("avt_profile"),
                    traffic_class=match.get("traffic_class"),
                    dscp=match.get("dscp"),
                )

            if (default_match := policy.get("default_match")) is not None:
                pathfinder_policy.matches.append_new(
                    application_profile=default_match.get("application_profile"),
                    avt_profile=default_match.get("avt_profile"),
                    traffic_class=default_match.get("traffic_class"),
                    dscp=default_match.get("dscp"),
                )

            self.structured_config.router_adaptive_virtual_topology.policies.append(pathfinder_policy)

    def _set_cv_pathfinder_profiles(self: AvdStructuredConfigNetworkServicesProtocol) -> None:
        """Set the router adaptive-virtual-topology profiles for this router."""
        for policy in self._filtered_wan_policies:
            for match in policy.get("matches", []):
                profile = EosCliConfigGen.RouterAdaptiveVirtualTopology.ProfilesItem(
                    name=match["avt_profile"], load_balance_policy=match["load_balance_policy"].name
                )

                if (internet_exit_policy_name := match["internet_exit_policy_name"]) is not None and internet_exit_policy_name in [
                    policy.name for policy, _connections in self._filtered_internet_exit_policies_and_connections
                ]:
                    profile.internet_exit_policy = internet_exit_policy_name
                self.structured_config.router_adaptive_virtual_topology.profiles.append(profile)

            if (default_match := policy.get("default_match")) is not None:
                profile = EosCliConfigGen.RouterAdaptiveVirtualTopology.ProfilesItem(
                    name=default_match["avt_profile"], load_balance_policy=default_match["load_balance_policy"].name
                )

                if (internet_exit_policy_name := default_match["internet_exit_policy_name"]) is not None and internet_exit_policy_name in [
                    policy.name for policy, _connections in self._filtered_internet_exit_policies_and_connections
                ]:
                    profile.internet_exit_policy = internet_exit_policy_name
                self.structured_config.router_adaptive_virtual_topology.profiles.append(profile)
