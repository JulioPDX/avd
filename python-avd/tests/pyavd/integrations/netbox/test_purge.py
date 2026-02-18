# Copyright (c) 2023-2026 Arista Networks, Inc.
# Use of this source code is governed by the Apache License 2.0
# that can be found in the LICENSE file.
# ruff: noqa: ANN001, ANN201
"""Tests for NetBox purge_all functionality."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyavd.integrations.netbox.async_sync import AsyncAVDNetBoxSync
from pyavd.integrations.netbox.models import SyncResult


@pytest.fixture
def mock_client():
    """Create a mock NetBox client."""
    client = MagicMock()
    client.get = AsyncMock()
    client.post = AsyncMock()
    client.patch = AsyncMock()
    client.delete = AsyncMock()
    client.get_all = MagicMock()  # Returns async generator
    client.get_all_list = AsyncMock()
    return client


@pytest.fixture
def sync_instance(mock_client):
    """Create an AsyncAVDNetBoxSync instance with mocked client."""
    return AsyncAVDNetBoxSync(
        client=mock_client,
        site_name="Test_Site",
        managed_tag="avd-managed",
    )


class TestPurgeAllSignature:
    """Tests for purge_all method signature."""

    def test_purge_prerequisites_parameter_exists(self, sync_instance):
        """Test that purge_all accepts purge_prerequisites parameter."""
        import inspect

        sig = inspect.signature(sync_instance.purge_all)
        params = list(sig.parameters.keys())
        assert "purge_prerequisites" in params
        assert "dry_run" in params

    def test_purge_prerequisites_default_is_false(self, sync_instance):
        """Test that purge_prerequisites defaults to False."""
        import inspect

        sig = inspect.signature(sync_instance.purge_all)
        param = sig.parameters["purge_prerequisites"]
        assert param.default is False


class TestPurgeAllDeletionOrder:
    """Tests for purge_all deletion order."""

    @pytest.mark.asyncio
    async def test_basic_deletion_order_without_prerequisites(self, sync_instance, mock_client):
        """Test standard deletion order without prerequisites."""
        # Mock tag lookup to return a tag
        mock_client.get.return_value = {"results": [{"id": "tag-123", "name": "avd-managed"}]}
        mock_client.get_all_list.return_value = []

        with patch.object(sync_instance, "_ensure_managed_tag", new_callable=AsyncMock) as mock_tag:
            mock_tag.return_value = "tag-123"
            await sync_instance.purge_all(purge_prerequisites=False)

        # Should only process standard objects (not prerequisites)
        calls = [call[0][0] for call in mock_client.get_all_list.call_args_list]

        # Verify standard endpoints are called
        assert any("/api/dcim/cables/" in call for call in calls)
        assert any("/api/dcim/devices/" in call for call in calls)
        assert any("/api/dcim/interfaces/" in call for call in calls)

        # Verify prerequisite endpoints are NOT called
        assert not any("/api/dcim/sites/" in call for call in calls)
        assert not any("/api/dcim/device-types/" in call for call in calls)
        assert not any("/api/dcim/manufacturers/" in call for call in calls)

    @pytest.mark.asyncio
    async def test_deletion_order_with_prerequisites(self, sync_instance, mock_client):
        """Test deletion order includes prerequisites when enabled."""
        mock_client.get.return_value = {"results": [{"id": "tag-123", "name": "avd-managed"}]}
        mock_client.get_all_list.return_value = []

        with patch.object(sync_instance, "_ensure_managed_tag", new_callable=AsyncMock) as mock_tag:
            mock_tag.return_value = "tag-123"
            await sync_instance.purge_all(purge_prerequisites=True)

        # Should process both standard objects AND prerequisites
        calls = [call[0][0] for call in mock_client.get_all_list.call_args_list]

        # Verify prerequisite endpoints ARE called
        assert any("/api/dcim/sites/" in call for call in calls)
        assert any("/api/dcim/device-types/" in call for call in calls)
        assert any("/api/dcim/manufacturers/" in call for call in calls)
        assert any("/api/dcim/device-roles/" in call for call in calls)
        assert any("/api/dcim/platforms/" in call for call in calls)


class TestPurgeAllDryRun:
    """Tests for purge_all dry_run functionality."""

    @pytest.mark.asyncio
    async def test_dry_run_does_not_delete(self, sync_instance, mock_client):
        """Test that dry_run mode does not actually delete objects."""
        mock_client.get_all_list.return_value = [
            {"id": 1, "name": "device1"},
            {"id": 2, "name": "device2"},
        ]

        with patch.object(sync_instance, "_ensure_managed_tag", new_callable=AsyncMock) as mock_tag:
            mock_tag.return_value = "tag-123"
            result = await sync_instance.purge_all(dry_run=True)

        # delete should NOT be called
        mock_client.delete.assert_not_called()
        # skipped count should reflect objects that would be deleted
        assert result.skipped > 0

    @pytest.mark.asyncio
    async def test_actual_delete_when_not_dry_run(self, sync_instance, mock_client):
        """Test that objects are deleted when dry_run is False."""
        mock_client.get_all_list.return_value = [
            {"id": 1, "name": "device1"},
        ]
        mock_client.delete.return_value = None

        with patch.object(sync_instance, "_ensure_managed_tag", new_callable=AsyncMock) as mock_tag:
            mock_tag.return_value = "tag-123"
            result = await sync_instance.purge_all(dry_run=False)

        # delete should be called
        assert mock_client.delete.called
        assert result.deleted > 0


class TestPurgeAllReturnsResult:
    """Tests for purge_all return value."""

    @pytest.mark.asyncio
    async def test_returns_sync_result(self, sync_instance, mock_client):
        """Test that purge_all returns a SyncResult."""
        mock_client.get_all_list.return_value = []

        with patch.object(sync_instance, "_ensure_managed_tag", new_callable=AsyncMock) as mock_tag:
            mock_tag.return_value = "tag-123"
            result = await sync_instance.purge_all()

        assert isinstance(result, SyncResult)
        assert hasattr(result, "deleted")
        assert hasattr(result, "skipped")
        assert hasattr(result, "errors")
