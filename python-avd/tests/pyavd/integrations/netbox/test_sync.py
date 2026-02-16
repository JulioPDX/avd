# Copyright (c) 2023-2026 Arista Networks, Inc.
# Use of this source code is governed by the Apache License 2.0
# that can be found in the LICENSE file.
# ruff: noqa: ANN201, ANN001
"""Tests for NetBox sync functionality."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from pyavd.integrations.netbox.sync import AVDNetBoxSync, SyncResult


class TestSyncResult:
    """Tests for SyncResult dataclass."""

    def test_default_values(self):
        result = SyncResult()
        assert result.created == 0
        assert result.updated == 0
        assert result.skipped == 0
        assert not result.errors

    def test_addition(self):
        result1 = SyncResult(created=1, updated=2, skipped=1)
        result2 = SyncResult(created=3, updated=1, errors=["error1"])
        combined = result1 + result2
        assert combined.created == 4
        assert combined.updated == 3
        assert combined.skipped == 1
        assert combined.errors == ["error1"]


class TestAVDNetBoxSync:
    """Tests for AVDNetBoxSync class."""

    @pytest.fixture
    def mock_client(self):
        """Create a mocked NetBox client."""
        client = MagicMock()
        client.get.return_value = {"results": [], "count": 0}
        client.get_all.return_value = []
        return client

    @pytest.fixture
    def sync(self, mock_client):
        """Create sync instance with mocked client."""
        return AVDNetBoxSync(
            client=mock_client,
            site_name="DC1",
            dry_run=False,
            create_prerequisites=False,
        )

    def test_init_defaults(self, mock_client):
        """Test default initialization."""
        sync = AVDNetBoxSync(mock_client)
        assert sync.dry_run is False
        assert sync.site_name is None
        assert sync.create_prerequisites is True  # Default is True

    def test_dry_run_skips_creation(self, mock_client):
        """Dry run should not create objects."""
        sync = AVDNetBoxSync(mock_client, dry_run=True)

        config = {"hostname": "spine1"}
        result = sync.sync_device(config, node_type="spine")

        assert result.skipped == 1
        assert result.created == 0
        mock_client.post.assert_not_called()

    def test_sync_device_missing_hostname(self, sync):
        """Device without hostname should add error."""
        config = {}
        result = sync.sync_device(config)

        assert len(result.errors) == 1
        assert "missing hostname" in result.errors[0].lower()


class TestAVDNetBoxSyncDevices:
    """Tests for device sync functionality."""

    @pytest.fixture
    def mock_client(self):
        client = MagicMock()
        client.get.return_value = {"results": [], "count": 0}
        client.get_all.return_value = []
        client.post.return_value = {"id": 1, "name": "spine1"}
        return client

    def test_sync_new_device(self, mock_client):
        """Syncing a new device should create it."""
        sync = AVDNetBoxSync(mock_client, site_name="DC1", create_prerequisites=False)

        # Mock prerequisites already in cache
        sync._cache["site"] = {"id": 1, "name": "DC1"}
        sync._cache["device_type"] = {"id": 1, "model": "vEOS"}
        sync._prerequisites_created = True

        config = {"hostname": "spine1"}
        result = sync.sync_device(config, node_type="spine")

        assert result.created == 1
        mock_client.post.assert_called_once()

    def test_sync_existing_device_updates(self, mock_client):
        """Syncing existing device should update it."""
        # Mock finding existing device
        mock_client.get.return_value = {"results": [{"id": 1, "name": "spine1"}], "count": 1}
        mock_client.patch.return_value = {"id": 1, "name": "spine1"}

        sync = AVDNetBoxSync(mock_client, create_prerequisites=False)
        sync._prerequisites_created = True

        config = {"hostname": "spine1"}
        result = sync.sync_device(config)

        assert result.updated == 1
        mock_client.patch.assert_called_once()


class TestParseVlanList:
    """Tests for _parse_vlan_list helper method."""

    @pytest.fixture
    def sync(self):
        client = MagicMock()
        return AVDNetBoxSync(client)

    def test_single_vlan(self, sync):
        """Single VLAN ID."""
        result = sync._parse_vlan_list("10")
        assert result == [10]

    def test_comma_separated(self, sync):
        """Comma-separated VLAN IDs."""
        result = sync._parse_vlan_list("10,20,30")
        assert result == [10, 20, 30]

    def test_vlan_range(self, sync):
        """VLAN range expansion."""
        result = sync._parse_vlan_list("10-15")
        assert result == [10, 11, 12, 13, 14, 15]

    def test_mixed_ranges_and_singles(self, sync):
        """Mixed ranges and single VLANs."""
        result = sync._parse_vlan_list("10-12,20,30-32")
        assert result == [10, 11, 12, 20, 30, 31, 32]

    def test_whitespace_handling(self, sync):
        """Handles whitespace in input."""
        result = sync._parse_vlan_list(" 10 , 20 , 30 ")
        assert result == [10, 20, 30]

    def test_invalid_vlan_ignored(self, sync):
        """Invalid VLAN entries are ignored."""
        result = sync._parse_vlan_list("10,invalid,20")
        assert result == [10, 20]

    def test_empty_string(self, sync):
        """Empty string returns empty list."""
        result = sync._parse_vlan_list("")
        assert result == []


class TestParseAsn:
    """Tests for _parse_asn helper method."""

    @pytest.fixture
    def sync(self):
        client = MagicMock()
        return AVDNetBoxSync(client)

    def test_integer_asn(self, sync):
        """Plain integer ASN."""
        result = sync._parse_asn(65001)
        assert result == 65001

    def test_string_asn(self, sync):
        """String integer ASN."""
        result = sync._parse_asn("65001")
        assert result == 65001

    def test_asdot_notation(self, sync):
        """Asdot notation ASN (e.g., 65001.100)."""
        result = sync._parse_asn("65001.100")
        # 65001 << 16 + 100 = 4259840100
        assert result == (65001 << 16) + 100

    def test_asdot_simple(self, sync):
        """Simple asdot notation."""
        result = sync._parse_asn("1.1")
        assert result == (1 << 16) + 1

    def test_invalid_asn_string(self, sync):
        """Invalid ASN string returns None."""
        result = sync._parse_asn("invalid")
        assert result is None

    def test_invalid_asdot(self, sync):
        """Invalid asdot format returns None."""
        result = sync._parse_asn("65001.invalid")
        assert result is None


class TestSyncPrefix:
    """Tests for sync_prefix method."""

    @pytest.fixture
    def mock_client(self):
        client = MagicMock()
        client.get.return_value = {"results": [], "count": 0}
        client.get_all.return_value = []
        client.post.return_value = {"id": 1, "prefix": "10.0.0.0/24"}
        return client

    def test_sync_new_prefix(self, mock_client):
        """Create a new prefix."""
        sync = AVDNetBoxSync(mock_client)
        result = sync.sync_prefix("10.0.0.0/24")

        assert result.created == 1
        mock_client.post.assert_called_once()

    def test_sync_prefix_with_vrf(self, mock_client):
        """Create prefix with VRF assignment."""
        mock_client.get.side_effect = [
            {"results": [{"id": 5, "name": "VRF10"}], "count": 1},  # VRF lookup
            {"results": [], "count": 0},  # Prefix lookup
        ]

        sync = AVDNetBoxSync(mock_client)
        result = sync.sync_prefix("10.0.0.0/24", vrf_name="VRF10")

        assert result.created == 1
        call_data = mock_client.post.call_args[0][1]
        assert call_data["vrf"] == 5

    def test_sync_existing_prefix_updates(self, mock_client):
        """Update existing prefix."""
        mock_client.get.return_value = {"results": [{"id": 1, "prefix": "10.0.0.0/24"}], "count": 1}
        mock_client.patch.return_value = {"id": 1}

        sync = AVDNetBoxSync(mock_client)
        result = sync.sync_prefix("10.0.0.0/24")

        assert result.updated == 1
        mock_client.patch.assert_called_once()

    def test_sync_invalid_prefix(self, mock_client):
        """Invalid prefix format returns error."""
        sync = AVDNetBoxSync(mock_client)
        result = sync.sync_prefix("invalid-prefix")

        assert len(result.errors) == 1
        assert "Invalid prefix" in result.errors[0]

    def test_sync_prefix_dry_run(self, mock_client):
        """Dry run skips creation."""
        sync = AVDNetBoxSync(mock_client, dry_run=True)
        result = sync.sync_prefix("10.0.0.0/24")

        assert result.skipped == 1
        mock_client.post.assert_not_called()


class TestSyncAsn:
    """Tests for sync_asn method."""

    @pytest.fixture
    def mock_client(self):
        client = MagicMock()
        client.get.return_value = {"results": [], "count": 0}
        client.get_all.return_value = []
        client.post.return_value = {"id": 1, "asn": 65001}
        return client

    def test_sync_new_asn(self, mock_client):
        """Create a new ASN."""
        # First call for ASN lookup, second for RIR lookup, third for RIR creation
        mock_client.get.return_value = {"results": [], "count": 0}
        mock_client.post.side_effect = [
            {"id": 1, "name": "Private", "slug": "private"},  # RIR creation
            {"id": 1, "asn": 65001},  # ASN creation
        ]

        sync = AVDNetBoxSync(mock_client)
        result = sync.sync_asn(65001)

        assert result.created == 1

    def test_sync_existing_asn_updates(self, mock_client):
        """Update existing ASN."""
        mock_client.get.side_effect = [
            {"results": [{"id": 1, "asn": 65001}], "count": 1},  # ASN lookup
            {"results": [{"id": 1, "slug": "private"}], "count": 1},  # RIR lookup
        ]
        mock_client.patch.return_value = {"id": 1}

        sync = AVDNetBoxSync(mock_client)
        result = sync.sync_asn(65001)

        assert result.updated == 1

    def test_sync_asn_asdot(self, mock_client):
        """Sync ASN in asdot notation."""
        mock_client.get.return_value = {"results": [], "count": 0}
        mock_client.post.side_effect = [
            {"id": 1, "slug": "private"},
            {"id": 1, "asn": 4259840100},
        ]

        sync = AVDNetBoxSync(mock_client)
        result = sync.sync_asn("65001.100")

        assert result.created == 1

    def test_sync_invalid_asn(self, mock_client):
        """Invalid ASN format returns error."""
        sync = AVDNetBoxSync(mock_client)
        result = sync.sync_asn("invalid")

        assert len(result.errors) == 1
        assert "Invalid ASN" in result.errors[0]

    def test_sync_asn_dry_run(self, mock_client):
        """Dry run skips creation."""
        sync = AVDNetBoxSync(mock_client, dry_run=True)
        result = sync.sync_asn(65001)

        assert result.skipped == 1
        mock_client.post.assert_not_called()


class TestSyncPortChannels:
    """Tests for sync_port_channels method."""

    @pytest.fixture
    def mock_client(self):
        client = MagicMock()
        client.get.return_value = {"results": [], "count": 0}
        client.get_all.return_value = []
        client.post.return_value = {"id": 10, "name": "Port-Channel3"}
        return client

    def test_sync_port_channel_creates_lag(self, mock_client):
        """Create LAG interface for port-channel."""
        mock_client.get.side_effect = [
            {"results": [{"id": 1, "name": "leaf1"}], "count": 1},  # Device lookup
            {"results": [], "count": 0},  # Port-channel interface lookup
        ]

        sync = AVDNetBoxSync(mock_client)
        config = {
            "hostname": "leaf1",
            "port_channel_interfaces": [
                {"name": "Port-Channel3", "description": "MLAG Peer"},
            ],
            "ethernet_interfaces": [],
        }
        result = sync.sync_port_channels(config)

        assert result.created == 1
        call_data = mock_client.post.call_args[0][1]
        assert call_data["type"] == "lag"
        assert call_data["name"] == "Port-Channel3"

    def test_sync_port_channel_assigns_members(self, mock_client):
        """Ethernet interfaces are assigned to LAG."""
        mock_client.get.side_effect = [
            {"results": [{"id": 1, "name": "leaf1"}], "count": 1},  # Device lookup
            {"results": [], "count": 0},  # Port-channel lookup
            {"results": [{"id": 20, "name": "Ethernet3"}], "count": 1},  # Eth3 lookup
            {"results": [{"id": 21, "name": "Ethernet4"}], "count": 1},  # Eth4 lookup
        ]
        mock_client.post.return_value = {"id": 10, "name": "Port-Channel3"}

        sync = AVDNetBoxSync(mock_client)
        config = {
            "hostname": "leaf1",
            "port_channel_interfaces": [
                {"name": "Port-Channel3", "description": "MLAG Peer"},
            ],
            "ethernet_interfaces": [
                {"name": "Ethernet3", "channel_group": {"id": 3, "mode": "active"}},
                {"name": "Ethernet4", "channel_group": {"id": 3, "mode": "active"}},
            ],
        }
        result = sync.sync_port_channels(config)

        assert result.created == 1
        # Check that member interfaces were patched with LAG
        assert mock_client.patch.call_count >= 2

    def test_sync_port_channel_no_device(self, mock_client):
        """No sync if device not found."""
        mock_client.get.return_value = {"results": [], "count": 0}

        sync = AVDNetBoxSync(mock_client)
        config = {
            "hostname": "unknown",
            "port_channel_interfaces": [{"name": "Port-Channel3"}],
        }
        result = sync.sync_port_channels(config)

        assert result.created == 0

    def test_sync_port_channel_dry_run(self, mock_client):
        """Dry run skips creation."""
        mock_client.get.return_value = {"results": [{"id": 1, "name": "leaf1"}], "count": 1}

        sync = AVDNetBoxSync(mock_client, dry_run=True)
        config = {
            "hostname": "leaf1",
            "port_channel_interfaces": [{"name": "Port-Channel3"}],
            "ethernet_interfaces": [],
        }
        result = sync.sync_port_channels(config)

        assert result.skipped == 1
        mock_client.post.assert_not_called()


class TestSyncInterfaceVlanAssociations:
    """Tests for sync_interface_vlan_associations method."""

    @pytest.fixture
    def mock_client(self):
        client = MagicMock()
        client.get.return_value = {"results": [], "count": 0}
        client.get_all.return_value = [
            {"id": 1, "vid": 10, "name": "VLAN10"},
            {"id": 2, "vid": 20, "name": "VLAN20"},
            {"id": 3, "vid": 30, "name": "VLAN30"},
        ]
        return client

    def test_sync_trunk_tagged_vlans(self, mock_client):
        """Trunk mode assigns tagged VLANs."""
        mock_client.get.side_effect = [
            {"results": [{"id": 1, "name": "leaf1"}], "count": 1},  # Device
            {"results": [{"id": 100, "name": "Ethernet1"}], "count": 1},  # Interface
        ]

        sync = AVDNetBoxSync(mock_client)
        config = {
            "hostname": "leaf1",
            "ethernet_interfaces": [
                {
                    "name": "Ethernet1",
                    "switchport": {"mode": "trunk", "trunk": {"allowed_vlan": "10,20"}},
                },
            ],
            "port_channel_interfaces": [],
        }
        result = sync.sync_interface_vlan_associations(config)

        assert result.updated == 1
        call_data = mock_client.patch.call_args[0][1]
        assert "tagged_vlans" in call_data
        assert 1 in call_data["tagged_vlans"]  # VLAN ID 10 -> NetBox ID 1
        assert 2 in call_data["tagged_vlans"]  # VLAN ID 20 -> NetBox ID 2

    def test_sync_access_untagged_vlan(self, mock_client):
        """Access mode assigns untagged VLAN."""
        mock_client.get.side_effect = [
            {"results": [{"id": 1, "name": "leaf1"}], "count": 1},  # Device
            {"results": [{"id": 100, "name": "Ethernet1"}], "count": 1},  # Interface
        ]

        sync = AVDNetBoxSync(mock_client)
        config = {
            "hostname": "leaf1",
            "ethernet_interfaces": [
                {
                    "name": "Ethernet1",
                    "switchport": {"mode": "access", "access_vlan": 10},
                },
            ],
            "port_channel_interfaces": [],
        }
        result = sync.sync_interface_vlan_associations(config)

        assert result.updated == 1
        call_data = mock_client.patch.call_args[0][1]
        assert call_data["untagged_vlan"] == 1  # VLAN ID 10 -> NetBox ID 1

    def test_sync_trunk_native_vlan(self, mock_client):
        """Trunk native VLAN becomes untagged."""
        mock_client.get.side_effect = [
            {"results": [{"id": 1, "name": "leaf1"}], "count": 1},  # Device
            {"results": [{"id": 100, "name": "Ethernet1"}], "count": 1},  # Interface
        ]

        sync = AVDNetBoxSync(mock_client)
        config = {
            "hostname": "leaf1",
            "ethernet_interfaces": [
                {
                    "name": "Ethernet1",
                    "switchport": {"mode": "trunk", "trunk": {"allowed_vlan": "10,20", "native_vlan": 30}},
                },
            ],
            "port_channel_interfaces": [],
        }
        result = sync.sync_interface_vlan_associations(config)

        assert result.updated == 1
        call_data = mock_client.patch.call_args[0][1]
        assert call_data["untagged_vlan"] == 3  # VLAN ID 30 -> NetBox ID 3

    def test_sync_no_device_found(self, mock_client):
        """No sync if device not found."""
        mock_client.get.return_value = {"results": [], "count": 0}

        sync = AVDNetBoxSync(mock_client)
        config = {
            "hostname": "unknown",
            "ethernet_interfaces": [{"name": "Ethernet1"}],
            "port_channel_interfaces": [],
        }
        result = sync.sync_interface_vlan_associations(config)

        assert result.updated == 0


class TestSyncPrefixesFromConfig:
    """Tests for sync_prefixes_from_config method."""

    @pytest.fixture
    def mock_client(self):
        client = MagicMock()
        client.get.return_value = {"results": [], "count": 0}
        client.get_all.return_value = []
        client.post.return_value = {"id": 1, "prefix": "10.0.0.0/32"}
        return client

    def test_extracts_loopback_prefixes(self, mock_client):
        """Extracts prefixes from loopback interfaces."""
        sync = AVDNetBoxSync(mock_client)
        config = {
            "loopback_interfaces": [
                {"name": "Loopback0", "ip_address": "10.255.0.1/32", "description": "ROUTER_ID"},
                {"name": "Loopback1", "ip_address": "10.255.1.1/32", "description": "VTEP"},
            ],
        }
        result = sync.sync_prefixes_from_config(config)

        assert result.created == 2

    def test_extracts_vlan_interface_prefixes(self, mock_client):
        """Extracts prefixes from VLAN interfaces."""
        mock_client.get.side_effect = [
            {"results": [{"id": 1, "name": "VRF10"}], "count": 1},  # VRF lookup
            {"results": [], "count": 0},  # Prefix lookup
        ]

        sync = AVDNetBoxSync(mock_client)
        config = {
            "vlan_interfaces": [
                {"name": "Vlan10", "ip_address": "10.10.10.1/24", "vrf": "VRF10"},
            ],
        }
        result = sync.sync_prefixes_from_config(config)

        assert result.created == 1

    def test_extracts_management_prefixes(self, mock_client):
        """Extracts prefixes from management interfaces."""
        sync = AVDNetBoxSync(mock_client)
        config = {
            "management_interfaces": [
                {"name": "Management1", "ip_address": "192.168.1.10/24"},
            ],
        }
        result = sync.sync_prefixes_from_config(config)

        assert result.created == 1

    def test_deduplicates_prefixes(self, mock_client):
        """Same prefix is not synced twice."""
        sync = AVDNetBoxSync(mock_client)
        config = {
            "loopback_interfaces": [
                {"name": "Loopback0", "ip_address": "10.255.0.1/32"},
                {"name": "Loopback1", "ip_address": "10.255.0.1/32"},  # Duplicate
            ],
        }
        result = sync.sync_prefixes_from_config(config)

        assert result.created == 1


class TestSyncAsnsFromConfig:
    """Tests for sync_asns_from_config method."""

    @pytest.fixture
    def mock_client(self):
        client = MagicMock()
        client.get.return_value = {"results": [], "count": 0}
        client.get_all.return_value = []
        client.post.side_effect = [
            {"id": 1, "slug": "private"},
            {"id": 1, "asn": 65001},
        ]
        return client

    def test_extracts_local_asn(self, mock_client):
        """Extracts local AS from router_bgp.as."""
        sync = AVDNetBoxSync(mock_client)
        config = {
            "router_bgp": {"as": 65001},
        }
        result = sync.sync_asns_from_config(config)

        assert result.created == 1

    def test_extracts_neighbor_asns(self, mock_client):
        """Extracts remote AS from BGP neighbors."""
        mock_client.post.side_effect = [
            {"id": 1, "slug": "private"},
            {"id": 1, "asn": 65001},
            {"id": 1, "slug": "private"},
            {"id": 2, "asn": 65002},
        ]

        sync = AVDNetBoxSync(mock_client)
        config = {
            "router_bgp": {
                "as": 65001,
                "neighbors": [
                    {"ip_address": "10.0.0.1", "remote_as": 65002},
                ],
            },
        }
        result = sync.sync_asns_from_config(config)

        assert result.created == 2

    def test_deduplicates_asns(self, mock_client):
        """Same ASN is not synced twice."""
        sync = AVDNetBoxSync(mock_client)
        config = {
            "router_bgp": {
                "as": 65001,
                "neighbors": [
                    {"ip_address": "10.0.0.1", "remote_as": 65001},  # Same as local
                ],
            },
        }
        result = sync.sync_asns_from_config(config)

        assert result.created == 1

    def test_no_bgp_config(self, mock_client):
        """No errors when router_bgp is missing."""
        sync = AVDNetBoxSync(mock_client)
        config = {}
        result = sync.sync_asns_from_config(config)

        assert result.created == 0
        assert len(result.errors) == 0


class TestExtractPortChannelId:
    """Tests for _extract_port_channel_id helper method."""

    @pytest.fixture
    def sync(self):
        client = MagicMock()
        return AVDNetBoxSync(client)

    def test_standard_format(self, sync):
        """Standard Port-Channel format."""
        assert sync._extract_port_channel_id("Port-Channel5") == 5

    def test_lowercase(self, sync):
        """Lowercase format."""
        assert sync._extract_port_channel_id("port-channel10") == 10

    def test_no_hyphen(self, sync):
        """Format without hyphen."""
        assert sync._extract_port_channel_id("PortChannel3") == 3

    def test_large_number(self, sync):
        """Large port-channel number."""
        assert sync._extract_port_channel_id("Port-Channel999") == 999

    def test_invalid_format(self, sync):
        """Invalid format returns None."""
        assert sync._extract_port_channel_id("Ethernet1") is None
        assert sync._extract_port_channel_id("Invalid") is None
