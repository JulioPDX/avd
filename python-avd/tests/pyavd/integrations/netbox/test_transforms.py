# Copyright (c) 2023-2026 Arista Networks, Inc.
# Use of this source code is governed by the Apache License 2.0
# that can be found in the LICENSE file.
# ruff: noqa: ANN201, FBT003
"""Tests for NetBox integration transform functions."""

from __future__ import annotations

import pytest

from pyavd.integrations.netbox.transforms import (
    apply_transform,
    get_nested_value,
    invert_bool,
    map_interface_mode,
    map_interface_type,
    map_vlan_status,
    parse_speed,
    set_nested_value,
    slugify,
)


class TestSlugify:
    """Tests for slugify function."""

    def test_basic_string(self):
        assert slugify("My Site Name") == "my-site-name"

    def test_underscores(self):
        assert slugify("data_center_1") == "data-center-1"

    def test_special_characters(self):
        assert slugify("Site #1 (Primary)") == "site-1-primary"

    def test_consecutive_hyphens(self):
        assert slugify("site--name") == "site-name"

    def test_leading_trailing_hyphens(self):
        assert slugify("-site-name-") == "site-name"

    def test_empty_string(self):
        assert slugify("") == ""

    def test_none(self):
        assert slugify(None) == ""


class TestInvertBool:
    """Tests for invert_bool function."""

    def test_true_to_false(self):
        assert invert_bool(True) is False

    def test_false_to_true(self):
        assert invert_bool(False) is True

    def test_none_returns_none(self):
        assert invert_bool(None) is None


class TestMapInterfaceMode:
    """Tests for map_interface_mode function."""

    def test_access(self):
        assert map_interface_mode("access") == "access"

    def test_trunk_to_tagged(self):
        assert map_interface_mode("trunk") == "tagged"

    def test_dot1q_tunnel(self):
        assert map_interface_mode("dot1q-tunnel") == "tagged"

    def test_unknown_mode_passthrough(self):
        assert map_interface_mode("hybrid") == "hybrid"

    def test_none(self):
        assert map_interface_mode(None) is None


class TestMapVlanStatus:
    """Tests for map_vlan_status function."""

    def test_active(self):
        assert map_vlan_status("active") == "active"

    def test_suspend_to_deprecated(self):
        assert map_vlan_status("suspend") == "deprecated"

    def test_none_defaults_active(self):
        assert map_vlan_status(None) == "active"

    def test_unknown_defaults_active(self):
        assert map_vlan_status("unknown") == "active"


class TestParseSpeed:
    """Tests for parse_speed function."""

    def test_10g(self):
        assert parse_speed("10g") == 10000000

    def test_100g(self):
        assert parse_speed("100g") == 100000000

    def test_25g(self):
        assert parse_speed("25g") == 25000000

    def test_1g(self):
        assert parse_speed("1g") == 1000000

    def test_100m(self):
        assert parse_speed("100m") == 100000

    def test_forced_10g(self):
        assert parse_speed("forced 10g") == 10000000

    def test_none(self):
        assert parse_speed(None) is None

    def test_invalid(self):
        assert parse_speed("auto") is None


class TestMapInterfaceType:
    """Tests for map_interface_type function."""

    def test_loopback(self):
        assert map_interface_type("Loopback0") == "virtual"

    def test_vlan(self):
        assert map_interface_type("Vlan100") == "virtual"

    def test_port_channel(self):
        assert map_interface_type("Port-Channel1") == "lag"

    def test_management(self):
        assert map_interface_type("Management1") == "1000base-t"

    def test_ethernet(self):
        assert map_interface_type("Ethernet1") == "other"

    def test_vxlan(self):
        assert map_interface_type("Vxlan1") == "virtual"

    def test_empty(self):
        assert map_interface_type("") == "other"


class TestGetNestedValue:
    """Tests for get_nested_value function."""

    def test_single_level(self):
        data = {"hostname": "spine1"}
        assert get_nested_value(data, "hostname") == "spine1"

    def test_nested(self):
        data = {"metadata": {"platform": "vEOS"}}
        assert get_nested_value(data, "metadata.platform") == "vEOS"

    def test_missing_key(self):
        data = {"hostname": "spine1"}
        assert get_nested_value(data, "missing") is None

    def test_missing_nested(self):
        data = {"metadata": {}}
        assert get_nested_value(data, "metadata.platform") is None


class TestSetNestedValue:
    """Tests for set_nested_value function."""

    def test_single_level(self):
        data = {}
        set_nested_value(data, "name", "spine1")
        assert data == {"name": "spine1"}

    def test_nested_creates_parents(self):
        data = {}
        set_nested_value(data, "custom_fields.system_mac", "00:00:00:00:00:01")
        assert data == {"custom_fields": {"system_mac": "00:00:00:00:00:01"}}


class TestApplyTransform:
    """Tests for apply_transform function."""

    def test_known_transform(self):
        assert apply_transform("slugify", "My Site") == "my-site"

    def test_unknown_transform_raises(self):
        with pytest.raises(ValueError, match="Unknown transform"):
            apply_transform("nonexistent", "value")
