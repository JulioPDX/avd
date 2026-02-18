# Copyright (c) 2023-2026 Arista Networks, Inc.
# Use of this source code is governed by the Apache License 2.0
# that can be found in the LICENSE file.
# ruff: noqa: ANN001, ANN201
"""Tests for Nautobot prefix helper methods."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from pyavd.integrations.nautobot.async_sync import AsyncAVDNautobotSync


@pytest.fixture
def mock_client():
    """Create a mock Nautobot client."""
    client = MagicMock()
    client.get = AsyncMock()
    client.post = AsyncMock()
    client.patch = AsyncMock()
    client.delete = AsyncMock()
    return client


@pytest.fixture
def sync_instance(mock_client):
    """Create an AsyncAVDNautobotSync instance with mocked client."""
    return AsyncAVDNautobotSync(
        client=mock_client,
        location_name="Test_Site",
        managed_tag="avd-managed",
        dry_run=True,  # Use dry_run to avoid actual API calls
    )


class TestGetParentPrefix:
    """Tests for _get_parent_prefix method."""

    def test_ipv4_slash_32_returns_slash_24(self, sync_instance):
        """Test that /32 IPv4 addresses return /24 parent prefix."""
        result = sync_instance._get_parent_prefix("10.255.0.1/32")
        assert result == "10.255.0.0/24"

    def test_ipv4_slash_24_returns_slash_24(self, sync_instance):
        """Test that /24 IPv4 addresses return /24 parent prefix."""
        result = sync_instance._get_parent_prefix("10.10.10.1/24")
        assert result == "10.10.10.0/24"

    def test_ipv4_different_subnet_returns_correct_parent(self, sync_instance):
        """Test various IPv4 subnets return correct /24 parent."""
        assert sync_instance._get_parent_prefix("192.168.1.100/28") == "192.168.1.0/24"
        assert sync_instance._get_parent_prefix("172.16.50.25/30") == "172.16.50.0/24"

    def test_ipv6_returns_slash_64(self, sync_instance):
        """Test that IPv6 addresses return /64 parent prefix."""
        result = sync_instance._get_parent_prefix("2001:db8::1/128")
        assert result == "2001:db8::/64"

    def test_ipv4_no_cidr_assumes_host(self, sync_instance):
        """Test that IPv4 without CIDR notation is handled."""
        # When no prefix is specified, ipaddress defaults to /32
        result = sync_instance._get_parent_prefix("10.0.0.1")
        assert result == "10.0.0.0/24"

    def test_invalid_address_returns_none(self, sync_instance):
        """Test that invalid addresses return None."""
        assert sync_instance._get_parent_prefix("not-an-ip") is None
        assert sync_instance._get_parent_prefix("") is None


class TestCollectAllIpsFromConfig:
    """Tests for _collect_all_ips_from_config method."""

    def test_collects_loopback_ips(self, sync_instance):
        """Test collection of loopback interface IPs."""
        config = {
            "loopback_interfaces": [
                {"name": "Loopback0", "ip_address": "10.255.0.1/32"},
                {"name": "Loopback1", "ip_address": "10.255.1.1/32"},
            ]
        }
        result = sync_instance._collect_all_ips_from_config(config)
        assert "10.255.0.1/32" in result
        assert "10.255.1.1/32" in result

    def test_collects_vlan_interface_ips(self, sync_instance):
        """Test collection of VLAN interface IPs."""
        config = {
            "vlan_interfaces": [
                {"name": "Vlan100", "ip_address": "10.10.100.1/24"},
            ]
        }
        result = sync_instance._collect_all_ips_from_config(config)
        assert "10.10.100.1/24" in result

    def test_collects_ethernet_interface_ips(self, sync_instance):
        """Test collection of ethernet interface IPs."""
        config = {
            "ethernet_interfaces": [
                {"name": "Ethernet1", "ip_address": "10.0.0.1/31"},
            ]
        }
        result = sync_instance._collect_all_ips_from_config(config)
        assert "10.0.0.1/31" in result

    def test_collects_virtual_ips_as_string(self, sync_instance):
        """Test collection of virtual IPs when specified as string."""
        config = {
            "vlan_interfaces": [
                {"name": "Vlan100", "ip_address_virtual": "10.10.100.254/24"},
            ]
        }
        result = sync_instance._collect_all_ips_from_config(config)
        assert "10.10.100.254/24" in result

    def test_collects_virtual_ips_as_list(self, sync_instance):
        """Test collection of virtual IPs when specified as list."""
        config = {
            "vlan_interfaces": [
                {"name": "Vlan100", "ip_address_virtual": ["10.10.100.252/24", "10.10.100.253/24"]},
            ]
        }
        result = sync_instance._collect_all_ips_from_config(config)
        assert "10.10.100.252/24" in result
        assert "10.10.100.253/24" in result

    def test_empty_config_returns_empty_set(self, sync_instance):
        """Test that empty config returns empty set."""
        result = sync_instance._collect_all_ips_from_config({})
        assert len(result) == 0

    def test_interfaces_without_ips_are_skipped(self, sync_instance):
        """Test that interfaces without IP addresses are skipped."""
        config = {
            "ethernet_interfaces": [
                {"name": "Ethernet1", "description": "No IP here"},
                {"name": "Ethernet2", "ip_address": "10.0.0.1/31"},
            ]
        }
        result = sync_instance._collect_all_ips_from_config(config)
        assert len(result) == 1
        assert "10.0.0.1/31" in result
