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
from anta.tests.interfaces import VerifyInterfacesStatus
from anta.tests.mlag import VerifyMlagStatus
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
    "VerifyInterfacesStatus",
    "VerifyLLDPNeighbors",
    "VerifyMlagStatus",
    "VerifyNTP",
    "VerifyReachability",
    "VerifyReloadCause",
    "VerifyRoutingProtocolModel",
    "VerifySpecificIPSecConn",
    "VerifyStunClientTranslation",
    "VerifyTemperature",
    "VerifyTransceiversTemperature",
]
