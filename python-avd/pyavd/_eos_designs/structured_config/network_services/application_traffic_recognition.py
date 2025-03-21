# Copyright (c) 2023-2025 Arista Networks, Inc.
# Use of this source code is governed by the Apache License 2.0
# that can be found in the LICENSE file.
from __future__ import annotations

from functools import cached_property
from typing import TYPE_CHECKING, Protocol

from pyavd._eos_cli_config_gen.schema import EosCliConfigGen
from pyavd._eos_designs.structured_config.structured_config_generator import structured_config_contributor
from pyavd._errors import AristaAvdInvalidInputsError
from pyavd._utils import get

if TYPE_CHECKING:
    from . import AvdStructuredConfigNetworkServicesProtocol


class ApplicationTrafficRecognitionMixin(Protocol):
    """
    Mixin Class used to generate structured config for one key.

    Class should only be used as Mixin to a AvdStructuredConfig class.
    """

    @structured_config_contributor
    def application_traffic_recognition(self: AvdStructuredConfigNetworkServicesProtocol) -> None:
        """Set structured config for application_traffic_recognition if wan router."""
        if not self.shared_utils.is_wan_router:
            return

        self._set_application_classification()

        self._set_control_plane_application_profile()

    #  self._wan_control_plane_application_profile is defined in utils.py
    @cached_property
    def _wan_control_plane_application(self: AvdStructuredConfigNetworkServicesProtocol) -> str:
        return "APP-CONTROL-PLANE"

    @cached_property
    def _wan_cp_app_dst_prefix(self: AvdStructuredConfigNetworkServicesProtocol) -> str:
        return "PFX-PATHFINDERS"

    @cached_property
    def _wan_cp_app_src_prefix(self: AvdStructuredConfigNetworkServicesProtocol) -> str:
        return "PFX-LOCAL-VTEP-IP"

    def _set_control_plane_application_profile(self: AvdStructuredConfigNetworkServicesProtocol) -> None:
        """
        Set an application-profile for control-plane in structured_config.

        * the device Pathfinders vtep_ips as destination for non Pathfinders.
        * the device Pathfinder vtep_ip as source.

        Create a structure as follow. If any object already exist, it is kept as defined by user and override the defaults.

        Edge and Transit:

            application_traffic_recognition:
              application_profiles:
                - name: APP-PROFILE-CONTROL-PLANE
                  applications:
                    - name: APP-CONTROL-PLANE
              applications:
                ipv4_applications:
                  - name: APP-CONTROL-PLANE
                    dest_prefix_set_name: PFX-PATHFINDERS
              field_sets:
                ipv4_prefixes:
                  - name: PFX-PATHFINDERS
                    prefix_values: [Pathfinder to which the router is connected vtep_ips]

        Pathfinder:

            application_traffic_recognition:
              application_profiles:
                - name: APP-PROFILE-CONTROL-PLANE
                  applications:
                    - name: APP-CONTROL-PLANE
              applications:
                ipv4_applications:
                  - name: APP-CONTROL-PLANE
                    src_prefix_set_name: PFX-LOCAL-VTEP-IP
              field_sets:
                ipv4_prefixes:
                  - name: PFX-LOCAL-VTEP-IP
                    prefix_values: [Pathfinder vtep_ip]
        """
        # Adding the application-profile
        if self._wan_control_plane_application_profile_name in self.structured_config.application_traffic_recognition.application_profiles:
            return

        application_profile_item = EosCliConfigGen.ApplicationTrafficRecognition.ApplicationProfilesItem(name=self._wan_control_plane_application_profile_name)
        application_profile_item.applications.append_new(name=self._wan_control_plane_application)
        self.structured_config.application_traffic_recognition.application_profiles.append(application_profile_item)

        # Adding the application
        if self._wan_control_plane_application in self.structured_config.application_traffic_recognition.applications.ipv4_applications:
            return

        if self.shared_utils.is_wan_client:
            self.structured_config.application_traffic_recognition.applications.ipv4_applications.append_new(
                name=self._wan_control_plane_application,
                dest_prefix_set_name=self._wan_cp_app_dst_prefix,
            )
            # Adding the field-set based on the connected Pathfinder router-ids
            if self._wan_cp_app_dst_prefix in self.structured_config.application_traffic_recognition.field_sets.ipv4_prefixes:
                return

            pathfinder_vtep_ips = [f"{wan_rs.vtep_ip}/32" for wan_rs in self.shared_utils.filtered_wan_route_servers]

            self.structured_config.application_traffic_recognition.field_sets.ipv4_prefixes.append_new(
                name=self._wan_cp_app_dst_prefix,
                prefix_values=EosCliConfigGen.ApplicationTrafficRecognition.FieldSets.Ipv4PrefixesItem.PrefixValues(
                    pathfinder_vtep_ips,
                ),
            )
        elif self.shared_utils.is_wan_server:
            self.structured_config.application_traffic_recognition.applications.ipv4_applications.append_new(
                name=self._wan_control_plane_application,
                src_prefix_set_name=self._wan_cp_app_src_prefix,
            )

            self.structured_config.application_traffic_recognition.field_sets.ipv4_prefixes.append_new(
                name=self._wan_cp_app_src_prefix,
                prefix_values=EosCliConfigGen.ApplicationTrafficRecognition.FieldSets.Ipv4PrefixesItem.PrefixValues([f"{self.shared_utils.vtep_ip}/32"]),
            )

    def _set_application_classification(self: AvdStructuredConfigNetworkServicesProtocol) -> None:
        """
        Based on the filtered policies local to the device, set the application-profiles relevant to the device in structured config.

        Supports only `application_classification.applications.ipv4_applications` for now.

        For applications - the existence cannot be verified as there are 4000+ applications built-in in the DPI engine used by EOS.
        """
        # Application profiles first
        atr = EosCliConfigGen.ApplicationTrafficRecognition()
        for policy in self._filtered_wan_policies:
            if policy.get("is_default") and self._wan_control_plane_application_profile_name in self.inputs.application_classification.application_profiles:
                application_profile_item = self.inputs.application_classification.application_profiles[self._wan_control_plane_application_profile_name]
                atr.application_profiles.append(application_profile_item)

            for match in get(policy, "matches", []):
                application_profile = get(match, "application_profile", required=True)

                if application_profile not in self.inputs.application_classification.application_profiles:
                    if application_profile == self._wan_control_plane_application_profile_name:
                        # Ignore for control plane as it could be injected later.
                        continue

                    msg = (
                        f"The application profile {application_profile} used in policy {policy['name']} "
                        "is not defined in 'application_classification.application_profiles'."
                    )
                    raise AristaAvdInvalidInputsError(msg)
                application_profile_item = self.inputs.application_classification.application_profiles[application_profile]
                atr.application_profiles.append(application_profile_item)

            if (default_match := policy.get("default_match")) is not None:
                application_profile = get(default_match, "application_profile", default="default")
                if application_profile != "default":
                    if application_profile not in self.inputs.application_classification.application_profiles:
                        msg = (
                            f"The application profile {application_profile} used in policy {policy['name']} "
                            "is not defined in 'application_classification.application_profiles'."
                        )
                        raise AristaAvdInvalidInputsError(msg)

                    application_profile_item = self.inputs.application_classification.application_profiles[application_profile]
                    atr.application_profiles.append(application_profile_item)

        self.structured_config.application_traffic_recognition.application_profiles.extend(atr.application_profiles)

        for application_profile in atr.application_profiles:
            for category in application_profile.categories:
                if category.name not in self.inputs.application_classification.categories:
                    msg = (
                        f"The application profile {application_profile.name} uses the category {category.name} "
                        "undefined in 'application_classification.categories'."
                    )
                    raise AristaAvdInvalidInputsError(msg)

                category_item = self.inputs.application_classification.categories[category.name]
                atr.categories.append(category_item)
                self.structured_config.application_traffic_recognition.categories.extend(atr.categories)
            # Applications in application profiles
            for application in application_profile.applications:
                if application.name in self.inputs.application_classification.applications.ipv4_applications:
                    application_item = self.inputs.application_classification.applications.ipv4_applications[application.name]
                    atr.applications.ipv4_applications.append(application_item)

        # Applications in categories
        for category in atr.categories:
            for application in category.applications:
                if application.name in self.inputs.application_classification.applications.ipv4_applications:
                    application_item = self.inputs.application_classification.applications.ipv4_applications[application.name]
                    atr.applications.ipv4_applications.append(application_item)

        self.structured_config.application_traffic_recognition.applications.ipv4_applications.extend(atr.applications.ipv4_applications)

        for application in atr.applications.ipv4_applications:
            for prefix_set_name in (application.src_prefix_set_name, application.dest_prefix_set_name):
                if not prefix_set_name:
                    continue

                if prefix_set_name not in self.inputs.application_classification.field_sets.ipv4_prefixes:
                    msg = (
                        f"The IPv4 prefix field set {prefix_set_name} used in the application {application} "
                        "is undefined in 'application_classification.fields_sets.ipv4_prefixes'."
                    )
                    raise AristaAvdInvalidInputsError(msg)

                ipv4_prefix_item = self.inputs.application_classification.field_sets.ipv4_prefixes[prefix_set_name]
                atr.field_sets.ipv4_prefixes.append(ipv4_prefix_item)

            for port_set_name in (
                application.udp_src_port_set_name,
                application.udp_dest_port_set_name,
                application.tcp_src_port_set_name,
                application.tcp_dest_port_set_name,
            ):
                if not port_set_name:
                    continue
                if port_set_name not in self.inputs.application_classification.field_sets.l4_ports:
                    msg = (
                        f"The L4 Ports field set {port_set_name} used in the application {application} "
                        "is undefined in 'application_classification.fields_sets.l4_ports'."
                    )
                    raise AristaAvdInvalidInputsError(msg)

                l4_port = self.inputs.application_classification.field_sets.l4_ports[port_set_name]
                atr.field_sets.l4_ports.append(l4_port)

        self.structured_config.application_traffic_recognition.field_sets.ipv4_prefixes.extend(atr.field_sets.ipv4_prefixes)
        self.structured_config.application_traffic_recognition.field_sets.l4_ports.extend(atr.field_sets.l4_ports)
