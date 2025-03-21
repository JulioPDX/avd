# Copyright (c) 2024-2025 Arista Networks, Inc.
# Use of this source code is governed by the Apache License 2.0
# that can be found in the LICENSE file.
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from pyavd._eos_cli_config_gen.schema import EosCliConfigGen
from pyavd._eos_designs.structured_config.structured_config_generator import structured_config_contributor

if TYPE_CHECKING:
    from . import AvdStructuredConfigNetworkServicesProtocol


class MetadataMixin(Protocol):
    """
    Mixin Class used to generate structured config for one key.

    Class should only be used as Mixin to a AvdStructuredConfig class.
    """

    @structured_config_contributor
    def metadata(self: AvdStructuredConfigNetworkServicesProtocol) -> None:
        """
        Set the metadata.cv_pathfinder for CV Pathfinder routers.

        Pathfinders will always have applications since we have the default control plane apps.
        Edge routers may have internet_exit_policies but not applications.
        """
        if not self.shared_utils.is_cv_pathfinder_router:
            return
        self.set_cv_pathfinder_metadata_internet_exit_policies()
        self.set_cv_pathfinder_metadata_applications()

    def set_cv_pathfinder_metadata_internet_exit_policies(
        self: AvdStructuredConfigNetworkServicesProtocol,
    ) -> None:
        """Set the metadata.cv_pathfinder.internet_exit_policies if available."""
        if not self._filtered_internet_exit_policies_and_connections:
            return

        for internet_exit_policy, connections in self._filtered_internet_exit_policies_and_connections:
            # Currently only supporting zscaler
            if internet_exit_policy.type != "zscaler":
                continue

            ufqdn, ipsec_key = self._get_ipsec_credentials(internet_exit_policy)
            exit_policy = EosCliConfigGen.Metadata.CvPathfinder.InternetExitPoliciesItem(
                name=internet_exit_policy.name,
                type=internet_exit_policy.type,
                city=self._zscaler_endpoints.device_location.city,
                country=self._zscaler_endpoints.device_location.country,
                upload_bandwidth=internet_exit_policy.zscaler.upload_bandwidth,
                download_bandwidth=internet_exit_policy.zscaler.download_bandwidth,
                firewall=internet_exit_policy.zscaler.firewall.enabled,
                ips_control=internet_exit_policy.zscaler.firewall.ips,
                acceptable_use_policy=internet_exit_policy.zscaler.acceptable_use_policy,
            )
            exit_policy.vpn_credentials.append_new(fqdn=ufqdn, vpn_type="UFQDN", pre_shared_key=ipsec_key)
            for connection in connections:
                exit_policy.tunnels.append_new(
                    name=f"Tunnel{connection['tunnel_id']}",
                    preference="Preferred" if connection["preference"] == "primary" else "Alternate",
                    endpoint=connection["endpoint"],
                )
            self.structured_config.metadata.cv_pathfinder.internet_exit_policies.append(exit_policy)

    def set_cv_pathfinder_metadata_applications(self: AvdStructuredConfigNetworkServicesProtocol) -> None:
        """Set the metadata.cv_pathfinder.applications if available."""
        if not self.shared_utils.is_cv_pathfinder_server or (atr := self.structured_config.application_traffic_recognition) is None:
            return
        applications = atr.applications
        user_defined_app_names = set(applications.ipv4_applications.keys())
        categories = atr.categories
        for profile in atr.application_profiles:
            application_profile = EosCliConfigGen.Metadata.CvPathfinder.Applications.ProfilesItem(name=profile.name)
            application_profile.transport_protocols.extend(profile.application_transports)
            for application in profile.applications:
                if application.name not in user_defined_app_names:
                    services = EosCliConfigGen.Metadata.CvPathfinder.Applications.ProfilesItem.BuiltinApplicationsItem.Services()
                    if application.service is not None:
                        services.append_new(application.service)
                    application_profile.builtin_applications.append_new(name=application.name, services=services)
                else:
                    application_profile.user_defined_applications.append_new(name=application.name)
            for category in profile.categories:
                services = EosCliConfigGen.Metadata.CvPathfinder.Applications.ProfilesItem.CategoriesItem.Services()
                if category.service is not None:
                    services.append_new(category.service)
                application_profile.categories.append_new(category=category.name, services=services)
            self.structured_config.metadata.cv_pathfinder.applications.profiles.append(application_profile)
        for category in categories:
            for application in category.applications:
                if application.name not in user_defined_app_names:
                    services = EosCliConfigGen.Metadata.CvPathfinder.Applications.Categories.BuiltinApplicationsItem.Services()
                    if application.service is not None:
                        services.append_new(application.service)
                    self.structured_config.metadata.cv_pathfinder.applications.categories.builtin_applications.append_new(
                        name=application.name, category=category.name, services=services
                    )

                if application.name in user_defined_app_names:
                    self.structured_config.metadata.cv_pathfinder.applications.categories.user_defined_applications.append_new(
                        name=application.name, category=category.name
                    )
