# Copyright (c) 2024-2025 Arista Networks, Inc.
# Use of this source code is governed by the Apache License 2.0
# that can be found in the LICENSE file.
"""Centralized package to import all the tests of the ANTA framework."""

from anta.tests.avt import VerifyAVTRole
from anta.tests.connectivity import VerifyLLDPNeighbors, VerifyReachability
from anta.tests.hardware import (
    VerifyEnvironmentSystemCooling,
    VerifyTemperature,
    VerifyTransceiversTemperature,
)
from anta.tests.interfaces import (
    VerifyIllegalLACP,
    VerifyInterfaceDiscards,
    VerifyInterfaceErrDisabled,
    VerifyInterfaceErrors,
    VerifyInterfacesStatus,
    VerifyInterfaceUtilization,
    VerifyPortChannels,
    VerifyStormControlDrops,
)
from anta.tests.mlag import (
    VerifyMlagConfigSanity,
    VerifyMlagDualPrimary,
    VerifyMlagInterfaces,
    VerifyMlagReloadDelay,
    VerifyMlagStatus,
)
from anta.tests.routing.bgp import VerifyBGPPeerSession
from anta.tests.routing.generic import VerifyRoutingProtocolModel
from anta.tests.security import VerifyAPIHttpsSSL, VerifySpecificIPSecConn
from anta.tests.stun import VerifyStunClientTranslation
from anta.tests.system import VerifyNTP, VerifyReloadCause

__all__ = [
    "VerifyAPIHttpsSSL",
    "VerifyAVTRole",
    "VerifyBGPPeerSession",
    "VerifyEnvironmentSystemCooling",
    "VerifyIllegalLACP",
    "VerifyInterfaceDiscards",
    "VerifyInterfaceErrDisabled",
    "VerifyInterfaceErrors",
    "VerifyInterfaceUtilization",
    "VerifyInterfacesStatus",
    "VerifyLLDPNeighbors",
    "VerifyMlagConfigSanity",
    "VerifyMlagDualPrimary",
    "VerifyMlagInterfaces",
    "VerifyMlagReloadDelay",
    "VerifyMlagStatus",
    "VerifyNTP",
    "VerifyPortChannels",
    "VerifyReachability",
    "VerifyReloadCause",
    "VerifyRoutingProtocolModel",
    "VerifySpecificIPSecConn",
    "VerifyStormControlDrops",
    "VerifyStunClientTranslation",
    "VerifyTemperature",
    "VerifyTransceiversTemperature",
]
