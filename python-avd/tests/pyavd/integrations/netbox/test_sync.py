# Copyright (c) 2023-2026 Arista Networks, Inc.
# Use of this source code is governed by the Apache License 2.0
# that can be found in the LICENSE file.
# ruff: noqa: ANN201, ANN001, RET504
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


class TestAVDNetBoxSyncNetboxToAvd:
    """Tests for NetBox to AVD sync functionality."""

    @pytest.fixture
    def mock_client(self):
        client = MagicMock()
        return client

    def test_fetch_devices_from_netbox(self, mock_client):
        """Should convert NetBox devices to AVD inventory format."""
        mock_client.get_all.return_value = [
            {
                "id": 1,
                "name": "spine1",
                "role": {"slug": "spine"},
                "site": {"name": "DC1"},
                "platform": {"slug": "veos"},
                "primary_ip": {"address": "192.168.1.1/24"},
            }
        ]

        sync = AVDNetBoxSync(mock_client)
        inventory = sync.fetch_devices_from_netbox()

        assert "spine1" in inventory
        assert inventory["spine1"]["ansible_host"] == "192.168.1.1"
        assert inventory["spine1"]["type"] == "spine"

    def test_generate_avd_inventory(self, mock_client):
        """Should generate proper AVD inventory structure."""
        mock_client.get_all.return_value = [
            {"id": 1, "name": "spine1", "role": {"slug": "spine"}},
            {"id": 2, "name": "leaf1", "role": {"slug": "leaf"}},
        ]

        sync = AVDNetBoxSync(mock_client)
        inventory = sync.generate_avd_inventory()

        assert "all" in inventory
        fabric = inventory["all"]["children"]["FABRIC"]["children"]
        assert "SPINES" in fabric
        assert "L3_LEAFS" in fabric

    def test_fetch_vlans_from_netbox(self, mock_client):
        """Should convert NetBox VLANs to AVD format."""
        mock_client.get_all.return_value = [
            {"vid": 100, "name": "DATA", "status": {"value": "active"}},
            {"vid": 200, "name": "VOICE", "status": {"value": "deprecated"}},
        ]

        sync = AVDNetBoxSync(mock_client)
        vlans = sync.fetch_vlans_from_netbox()

        assert len(vlans) == 2
        assert vlans[0]["id"] == 100
        assert vlans[0]["state"] == "active"
        assert vlans[1]["state"] == "suspend"
