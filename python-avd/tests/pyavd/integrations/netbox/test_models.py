# Copyright (c) 2023-2026 Arista Networks, Inc.
# Use of this source code is governed by the Apache License 2.0
# that can be found in the LICENSE file.
# ruff: noqa: ANN201
"""Tests for NetBox integration models."""

from __future__ import annotations

from pyavd.integrations.netbox.models import (
    INTERFACE_MODE_MAP,
    NODE_TYPE_TO_DEVICE_ROLE,
    VLAN_STATUS_MAP,
    AVDNetBoxMapping,
    FieldMapping,
)


class TestFieldMapping:
    """Tests for FieldMapping dataclass."""

    def test_basic_mapping(self):
        mapping = FieldMapping("hostname", "name")
        assert mapping.avd_path == "hostname"
        assert mapping.netbox_field == "name"
        assert mapping.transform is None

    def test_mapping_with_transform(self):
        mapping = FieldMapping("metadata.platform", "platform.slug", transform="slugify")
        assert mapping.transform == "slugify"


class TestAVDNetBoxMapping:
    """Tests for AVDNetBoxMapping dataclass."""

    def test_default_device_mappings(self):
        mapping = AVDNetBoxMapping()
        assert len(mapping.device_mappings) > 0

        # Check hostname mapping exists
        hostname_mapping = next((m for m in mapping.device_mappings if m.avd_path == "hostname"), None)
        assert hostname_mapping is not None
        assert hostname_mapping.netbox_field == "name"

    def test_default_interface_mappings(self):
        mapping = AVDNetBoxMapping()
        assert len(mapping.interface_mappings) > 0

        # Check shutdown -> enabled mapping with invert
        shutdown_mapping = next((m for m in mapping.interface_mappings if m.avd_path == "shutdown"), None)
        assert shutdown_mapping is not None
        assert shutdown_mapping.netbox_field == "enabled"
        assert shutdown_mapping.transform == "invert_bool"

    def test_default_vlan_mappings(self):
        mapping = AVDNetBoxMapping()
        assert len(mapping.vlan_mappings) > 0

        # Check id -> vid mapping
        id_mapping = next((m for m in mapping.vlan_mappings if m.avd_path == "id"), None)
        assert id_mapping is not None
        assert id_mapping.netbox_field == "vid"

    def test_get_netbox_endpoints(self):
        endpoints = AVDNetBoxMapping.get_netbox_endpoints()

        assert "devices" in endpoints
        assert "interfaces" in endpoints
        assert "ip_addresses" in endpoints
        assert "vlans" in endpoints
        assert "vrfs" in endpoints
        assert "prefixes" in endpoints
        assert "sites" in endpoints
        assert "cables" in endpoints

        # Check endpoint format
        assert endpoints["devices"] == "/api/dcim/devices/"
        assert endpoints["vlans"] == "/api/ipam/vlans/"


class TestNodeTypeMapping:
    """Tests for NODE_TYPE_TO_DEVICE_ROLE mapping."""

    def test_spine(self):
        assert NODE_TYPE_TO_DEVICE_ROLE["spine"] == "spine"

    def test_l3leaf(self):
        assert NODE_TYPE_TO_DEVICE_ROLE["l3leaf"] == "leaf"

    def test_l2leaf(self):
        assert NODE_TYPE_TO_DEVICE_ROLE["l2leaf"] == "leaf"

    def test_super_spine(self):
        assert NODE_TYPE_TO_DEVICE_ROLE["super_spine"] == "super-spine"


class TestInterfaceModeMap:
    """Tests for INTERFACE_MODE_MAP."""

    def test_access(self):
        assert INTERFACE_MODE_MAP["access"] == "access"

    def test_trunk(self):
        assert INTERFACE_MODE_MAP["trunk"] == "tagged"


class TestVlanStatusMap:
    """Tests for VLAN_STATUS_MAP."""

    def test_active(self):
        assert VLAN_STATUS_MAP["active"] == "active"

    def test_suspend(self):
        assert VLAN_STATUS_MAP["suspend"] == "deprecated"
