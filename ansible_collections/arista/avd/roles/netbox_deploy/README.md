---
# This title is used for search results
title: Ansible Collection Role netbox_deploy
---
<!--
  ~ Copyright (c) 2026 Arista Networks, Inc.
  ~ Use of this source code is governed by the Apache License 2.0
  ~ that can be found in the LICENSE file.
  -->

# arista.avd.netbox_deploy

## Overview

**arista.avd.netbox_deploy** deploys AVD structured configuration data to NetBox DCIM/IPAM models.

The role synchronizes the following objects from AVD to NetBox:

- **Devices**: Platform, serial number, device role, and status
- **Interfaces**: Ethernet, Loopback, Management, and VLAN interfaces with all settings
- **VLANs**: VLAN ID, name, and status
- **VRFs**: Name and description
- **IP Addresses**: Assigned to interfaces with proper VRF assignment
- **Cables**: Physical connections between device interfaces

## Requirements

This role requires the `httpx` Python library:

```bash
pip install 'pyavd[netbox]'
# Or install httpx directly
pip install httpx
```

## Example

This basic example will deploy configurations for all devices in the structured configs directory to NetBox:

```yaml title="playbook.yml"
- name: Deploy to NetBox
  hosts: localhost
  gather_facts: false
  tasks:
    - name: Deploy AVD configs to NetBox
      ansible.builtin.import_role:
        name: arista.avd.netbox_deploy
      vars:
        netbox_url: "https://netbox.example.com"
        netbox_token: "{{ vault_netbox_token }}"  # Use Ansible Vault
        netbox_site_name: "DC1"
```

## Role Inputs

### Required Variables

| Variable | Description |
| -------- | ----------- |
| `netbox_url` | URL of the NetBox instance |
| `netbox_token` | NetBox API token (use Ansible Vault) |
| `netbox_site_name` | Name of the site in NetBox (or use `netbox_site_mapping` for multi-site) |

> **Note**: Either `netbox_site_name` or `netbox_site_mapping` must be provided.

### Optional Variables

| Variable | Default | Description |
| -------- | ------- | ----------- |
| `netbox_site_mapping` | - | Dict mapping hostname prefix to site name (for multi-site deployments) |
| `netbox_verify_ssl` | `true` | Verify SSL certificates |
| `netbox_timeout` | `30.0` | HTTP timeout in seconds |
| `netbox_create_prerequisites` | `true` | Auto-create site, device types, etc. |
| `netbox_dry_run` | `false` | Preview changes without applying |
| `netbox_devices` | All devices | List of specific devices to sync |
| `netbox_node_type_mapping` | Auto-inferred | Explicit hostname to node type mapping |
| `netbox_return_details` | `false` | Return detailed sync information |
| `netbox_fail_on_errors` | `false` | Fail playbook when there are sync errors |
| `netbox_display_results` | `true` | Display sync results summary at end of role |
| `netbox_reconcile` | `false` | Delete orphaned objects from NetBox |
| `netbox_managed_tag` | `avd-managed` | Tag name for AVD-managed objects |
| `netbox_use_async` | `true` | Use async HTTP client for better performance (4-8x faster) |
| `netbox_max_concurrent` | `10` | Maximum concurrent API requests when using async mode |
| `netbox_purge` | `false` | Delete ALL objects with managed tag (destructive) |
| `netbox_devicetype_library_url` | - | URL for NetBox Community Device Type Library (disabled by default) |
| `netbox_platform_mapping` | - | Dict mapping AVD platform names to library device type model names |

### Directory Configuration

The role defaults to the standard AVD directory structure:

```yaml
structured_dir: '{{ inventory_dir }}/intended/structured_configs'
```

This default works automatically when running in the standard AVD workflow. Override only if your
structured configs are in a different location.

## Node Type Inference

When `netbox_node_type_mapping` is not provided, node types are inferred from hostname patterns:

| Pattern | Node Type |
| ------- | --------- |
| `*spine*` | spine |
| `*l2spine*` or `*l2-spine*` | l2spine |
| `*l3spine*` or `*l3-spine*` | l3spine |
| `*leaf*` ending in `c` | l2leaf |
| `*leaf*` | l3leaf |
| `p*` (not `pe*`) | p (MPLS provider) |
| `pe*` | pe (MPLS provider edge) |
| `*rr*` (not `*wan*`) | rr (route reflector) |
| `*wan*rr*` | wan_rr |
| `*wan*` | wan_router |

## Role Outputs

The role registers results in the `netbox_deploy_results` variable:

```yaml
netbox_deploy_results:
  changed: true
  created: 42
  updated: 10
  skipped: 5
  errors: []
  msg: "Sync completed: 42 created, 10 updated, 5 skipped"
  # When netbox_return_details is true:
  devices:
    - dc1-spine1
    - dc1-leaf1a
  node_types:
    dc1-spine1: spine
    dc1-leaf1a: l3leaf
  dry_run: false
```

## NetBox API Token

The role supports both v1 (legacy) and v2 API tokens:

- **v1 tokens**: Plain token string (sent as `Token <token>`)
- **v2 tokens**: Format `nbt_xxx.yyy` (sent as `Bearer nbt_xxx.yyy`)

Token format is auto-detected based on the prefix.

## Dry Run Mode

Use `netbox_dry_run: true` or Ansible check mode (`--check`) to preview changes:

```yaml
- name: Preview NetBox changes
  ansible.builtin.import_role:
    name: arista.avd.netbox_deploy
  vars:
    netbox_url: "https://netbox.example.com"
    netbox_token: "{{ vault_netbox_token }}"
    netbox_site_name: "DC1"
    netbox_dry_run: true
    netbox_return_details: true
```

## Workflow Integration

The role is designed to run after `eos_designs` and `eos_cli_config_gen` in a typical AVD workflow:

```yaml
- name: Build AVD Configurations
  hosts: FABRIC
  gather_facts: false
  tasks:
    - name: Generate AVD structured configs
      ansible.builtin.import_role:
        name: arista.avd.eos_designs

    - name: Generate EOS configs
      ansible.builtin.import_role:
        name: arista.avd.eos_cli_config_gen

- name: Sync to NetBox
  hosts: localhost
  connection: local
  gather_facts: false
  vars:
    netbox_url: "https://netbox.example.com"
    netbox_token: "{{ vault_netbox_token }}"
    netbox_site_name: "DC1"
  tasks:
    - name: Deploy to NetBox
      ansible.builtin.import_role:
        name: arista.avd.netbox_deploy
```

> **Note**: The NetBox sync play uses `hosts: localhost` to avoid inheriting
> network device connection settings from group_vars. The `structured_dir` defaults
> to `{{ inventory_dir }}/intended/structured_configs` which works automatically
> in standard AVD workflows.

## Multi-Site Deployments

For multi-site deployments where devices from different sites are in the same structured configs directory, use `netbox_site_mapping` to assign devices to sites based on hostname prefix:

```yaml
- name: Sync Multi-DC to NetBox
  hosts: localhost
  connection: local
  gather_facts: false
  vars:
    netbox_url: "https://netbox.example.com"
    netbox_token: "{{ vault_netbox_token }}"
    # Map hostname prefixes to NetBox sites
    netbox_site_mapping:
      dc1: "DC1_Site"
      dc2: "DC2_Site"
  tasks:
    - name: Deploy to NetBox
      ansible.builtin.import_role:
        name: arista.avd.netbox_deploy
```

Devices with hostnames starting with `dc1` (e.g., `dc1-spine1`, `dc1-leaf1a`) will be assigned to "DC1_Site",
while devices starting with `dc2` will be assigned to "DC2_Site".

## Reconciliation (Garbage Collection)

When `netbox_reconcile: true`, the role will delete objects from NetBox that are tagged with
`netbox_managed_tag` (default: `avd-managed`) but no longer exist in the AVD structured configs.

This is useful for cleaning up orphaned objects when devices, interfaces, VLANs, or other
objects are removed from your AVD configurations.

### How It Works

1. All objects synced to NetBox are automatically tagged with the managed tag (`avd-managed`)
2. During sync, the role tracks which objects were touched
3. At the end of sync (if `netbox_reconcile: true`), objects with the managed tag that weren't
   touched are deleted

### Deletion Order

Objects are deleted in reverse dependency order to avoid foreign key errors:

1. Cables
2. IP Addresses
3. Interfaces
4. Prefixes
5. VLANs
6. VRFs
7. ASNs
8. Devices

### Example with Reconciliation

```yaml
- name: Sync to NetBox with cleanup
  hosts: localhost
  connection: local
  gather_facts: false
  vars:
    netbox_url: "https://netbox.example.com"
    netbox_token: "{{ vault_netbox_token }}"
    netbox_site_name: "DC1"
    netbox_reconcile: true  # Delete orphaned objects
    # netbox_managed_tag: "custom-tag"  # Optional: use a custom tag name
  tasks:
    - name: Deploy to NetBox
      ansible.builtin.import_role:
        name: arista.avd.netbox_deploy
```

> **Warning**: Use `netbox_dry_run: true` first to preview what would be deleted before
> enabling reconciliation in production.

## Purge Mode

The `netbox_purge: true` option deletes **ALL** objects tagged with `netbox_managed_tag` from NetBox
without performing any sync. This is useful for:

- Cleaning up a NetBox instance before migrating to a different source of truth
- Starting fresh when restructuring your AVD configurations
- Removing all AVD-managed data before decommissioning a fabric

### Example: Purge All AVD-Managed Objects

```yaml
- name: Purge AVD objects from NetBox
  hosts: localhost
  connection: local
  gather_facts: false
  vars:
    netbox_url: "https://netbox.example.com"
    netbox_token: "{{ vault_netbox_token }}"
    netbox_purge: true  # Delete ALL avd-managed objects
  tasks:
    - name: Purge NetBox
      ansible.builtin.import_role:
        name: arista.avd.netbox_deploy
```

> **Warning**: This is a destructive operation. Use `netbox_dry_run: true` first to preview
> what would be deleted.

When purge mode is enabled:

- `structured_config_dir` and `site_name`/`site_mapping` are not required
- Objects are deleted in reverse dependency order (cables → IPs → interfaces → etc.)
- Only objects with the managed tag are affected; manually-created objects are preserved

## Device Type Library Integration

The role can optionally fetch device type definitions from the
[NetBox Community Device Type Library](https://github.com/netbox-community/devicetype-library)
to create detailed device types with physical specifications (u_height, weight, airflow, etc.).

### Enabling Library Fetch

Set `netbox_devicetype_library_url` to enable fetching device types from the library:

```yaml
- name: Sync with device type library
  ansible.builtin.import_role:
    name: arista.avd.netbox_deploy
  vars:
    netbox_url: "https://netbox.example.com"
    netbox_token: "{{ vault_netbox_token }}"
    netbox_site_name: "DC1"
    netbox_devicetype_library_url: "https://raw.githubusercontent.com/netbox-community/devicetype-library/master/device-types/Arista"
```

### Platform Mapping

AVD uses short platform names (e.g., `7050SX3`) while the library uses full model names
(e.g., `DCS-7050SX3-48YC12-F`). Use `netbox_platform_mapping` to map AVD platform names to
library model names:

```yaml
netbox_devicetype_library_url: "https://raw.githubusercontent.com/netbox-community/devicetype-library/master/device-types/Arista"
netbox_platform_mapping:
  "7050SX3": "DCS-7050SX3-48YC12-F"
  "720XP": "DCS-720XP-48ZC6-F"
  "7280SR3": "DCS-7280SR3-48YC8-F"
```

### Offline Environments

By default, the library fetch is disabled (no internet access required). The role will
create simple device types using the AVD `metadata.platform` value. To explicitly disable
library fetch, omit `netbox_devicetype_library_url` or don't define it.

## Performance

The role uses async HTTP requests by default for significantly better performance. With async mode enabled
(`netbox_use_async: true`, the default), multiple devices are synced concurrently, resulting in 4-8x faster
sync times compared to sequential processing.

| Mode | 16 Devices | 32 Devices |
| ---- | ---------- | ---------- |
| Sync (sequential) | ~16 seconds | ~32 seconds |
| Async (concurrent) | ~3-4 seconds | ~6-8 seconds |

### Tuning Concurrency

The `netbox_max_concurrent` parameter controls how many API requests can be made simultaneously:

- **Lower values** (5-10): Safer for shared NetBox instances, less server load
- **Higher values** (20-50): Faster sync but may overwhelm the NetBox server

```yaml
- name: High-performance sync
  ansible.builtin.import_role:
    name: arista.avd.netbox_deploy
  vars:
    netbox_url: "https://netbox.example.com"
    netbox_token: "{{ vault_netbox_token }}"
    netbox_site_name: "DC1"
    netbox_max_concurrent: 20  # Increase for faster sync
```

To disable async mode (use sequential processing):

```yaml
netbox_use_async: false
```

## License

Project is published under [Apache 2.0 License](https://github.com/aristanetworks/avd/blob/devel/LICENSE)
