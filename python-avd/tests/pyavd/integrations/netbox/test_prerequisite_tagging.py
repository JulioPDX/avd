# Copyright (c) 2023-2026 Arista Networks, Inc.
# Use of this source code is governed by the Apache License 2.0
# that can be found in the LICENSE file.
# ruff: noqa: ANN001, ANN201
"""Tests for NetBox prerequisite object tagging functionality."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyavd.integrations.netbox.async_sync import AsyncAVDNetBoxSync


@pytest.fixture
def mock_client():
    """Create a mock NetBox client."""
    client = MagicMock()
    client.get = AsyncMock()
    client.post = AsyncMock(return_value={"id": 1})
    client.patch = AsyncMock()
    client.delete = AsyncMock()
    client.get_all = MagicMock()
    client.get_all_list = AsyncMock(return_value=[])
    return client


@pytest.fixture
def sync_instance(mock_client):
    """Create an AsyncAVDNetBoxSync instance with mocked client."""
    return AsyncAVDNetBoxSync(
        client=mock_client,
        site_name="Test_Site",
        managed_tag="avd-managed",
        create_prerequisites=True,
    )


class TestSiteTagging:
    """Tests for site creation with managed tag."""

    @pytest.mark.asyncio
    async def test_get_or_create_site_applies_tag(self, sync_instance, mock_client):
        """Test that _get_or_create_site applies managed tag when creating."""
        # Setup: No existing site, tag exists
        mock_client.get.return_value = {"results": []}
        mock_client.post.return_value = {"id": 1, "name": "New_Site", "slug": "new_site"}

        with patch.object(sync_instance, "_ensure_managed_tag", new_callable=AsyncMock) as mock_tag:
            mock_tag.return_value = 123
            await sync_instance._get_or_create_site("New_Site")

        # Verify post was called with tags
        post_calls = mock_client.post.call_args_list
        assert len(post_calls) > 0
        # Find the site creation call
        for call in post_calls:
            args, kwargs = call
            if "sites" in args[0]:
                data = args[1] if len(args) > 1 else kwargs.get("data", {})
                assert "tags" in data
                assert 123 in data["tags"]
                break


class TestEnsurePrerequisitesTagging:
    """Tests for _ensure_prerequisites tagging of objects."""

    @pytest.mark.asyncio
    async def test_manufacturer_created_with_tag(self, sync_instance, mock_client):
        """Test that manufacturers are created with managed tag."""
        mock_client.get.return_value = {"results": []}
        mock_client.post.return_value = {"id": 1}

        with patch.object(sync_instance, "_ensure_managed_tag", new_callable=AsyncMock) as mock_tag:
            mock_tag.return_value = 456

            # Reset prerequisites flag
            sync_instance._prerequisites_created = False
            await sync_instance._ensure_prerequisites()

        # Find manufacturer creation call
        for call in mock_client.post.call_args_list:
            args = call[0]
            if "manufacturers" in args[0]:
                data = args[1]
                assert "tags" in data
                assert 456 in data["tags"]
                break

    @pytest.mark.asyncio
    async def test_device_role_created_with_tag(self, sync_instance, mock_client):
        """Test that device roles are created with managed tag."""
        mock_client.get.return_value = {"results": []}
        mock_client.post.return_value = {"id": 1}

        with patch.object(sync_instance, "_ensure_managed_tag", new_callable=AsyncMock) as mock_tag:
            mock_tag.return_value = 789

            sync_instance._prerequisites_created = False
            await sync_instance._ensure_prerequisites()

        # Find device role creation calls (there are multiple roles)
        role_created_with_tag = False
        for call in mock_client.post.call_args_list:
            args = call[0]
            if "device-roles" in args[0]:
                data = args[1]
                if "tags" in data and 789 in data["tags"]:
                    role_created_with_tag = True
                    break

        assert role_created_with_tag, "Device roles should be created with managed tag"


class TestPlatformTagging:
    """Tests for platform creation with managed tag."""

    @pytest.mark.asyncio
    async def test_handle_platform_applies_tag(self, sync_instance, mock_client):
        """Test that _handle_platform applies managed tag when creating."""
        # Mock: no existing platform
        sync_instance._cache = {"platforms": {}}
        mock_client.post.return_value = {"id": 1, "name": "EOS", "slug": "eos"}

        with patch.object(sync_instance, "_get_or_cache", new_callable=AsyncMock) as mock_cache:
            mock_cache.return_value = {}  # No existing platforms
            with patch.object(sync_instance, "_ensure_managed_tag", new_callable=AsyncMock) as mock_tag:
                mock_tag.return_value = 321

                device_data = {}
                endpoints = sync_instance.mapping.get_netbox_endpoints()
                await sync_instance._handle_platform(device_data, endpoints)

        # Verify post was called with tags
        for call in mock_client.post.call_args_list:
            args = call[0]
            if "platforms" in args[0]:
                data = args[1]
                assert "tags" in data
                assert 321 in data["tags"]
                break
