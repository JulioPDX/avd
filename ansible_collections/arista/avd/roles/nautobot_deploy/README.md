---
# This title is used for search results
title: Ansible Collection Role nautobot_deploy
---
<!--
  ~ Copyright (c) 2026 Arista Networks, Inc.
  ~ Use of this source code is governed by the Apache License 2.0
  ~ that can be found in the LICENSE file.
  -->

# arista.avd.nautobot_deploy

## Overview

**arista.avd.nautobot_deploy** deploys AVD structured configuration data to Nautobot DCIM/IPAM models.

The role synchronizes the following objects from AVD to Nautobot:

- **Devices**: Platform, serial number, device role, and status
- **Interfaces**: Ethernet, Loopback, Management, and VLAN interfaces with all settings
- **VLANs**: VLAN ID, name, and status
- **VRFs**: Name and description
- **IP Addresses**: Assigned to interfaces with proper VRF and namespace assignment
- **Cables**: Physical connections between device interfaces

## Requirements

This role requires the `httpx` Python library:

```bash
pip install 'pyavd[nautobot]'
# Or install httpx directly
pip install httpx
```

## Example

This basic example will deploy configurations for all devices in the structured configs directory to Nautobot:

```yaml title="playbook.yml"
- name: Deploy to Nautobot
  hosts: localhost
  gather_facts: false
  tasks:
    - name: Deploy AVD configs to Nautobot
      ansible.builtin.import_role:
        name: arista.avd.nautobot_deploy
      vars:
        nautobot_url: "https://nautobot.example.com"
        nautobot_token: "{{ vault_nautobot_token }}"  # Use Ansible Vault
        nautobot_location_name: "DC1"
```

## Role Inputs

### Required Variables

| Variable | Description |
| -------- | ----------- |
| `nautobot_url` | URL of the Nautobot instance |
| `nautobot_token` | Nautobot API token (use Ansible Vault) |
| `nautobot_location_name` | Name of the location in Nautobot (or use `nautobot_location_mapping` for multi-site) |

> **Note**: Either `nautobot_location_name` or `nautobot_location_mapping` must be provided.

### Optional Variables

| Variable | Default | Description |
| -------- | ------- | ----------- |
| `nautobot_location_mapping` | - | Dict mapping hostname prefix to location name (for multi-site deployments) |
| `nautobot_verify_ssl` | `true` | Verify SSL certificates |
| `nautobot_timeout` | `30.0` | HTTP timeout in seconds |
| `nautobot_create_prerequisites` | `true` | Auto-create location, device types, etc. |
| `nautobot_dry_run` | `false` | Preview changes without applying |
| `nautobot_devices` | All devices | List of specific devices to sync |
| `nautobot_node_type_mapping` | Auto-inferred | Explicit hostname to node type mapping |
| `nautobot_return_details` | `false` | Return detailed sync information |
| `nautobot_fail_on_errors` | `false` | Fail playbook when there are sync errors |
| `nautobot_display_results` | `true` | Display sync results summary at end of role |
| `nautobot_reconcile` | `false` | Delete orphaned objects from Nautobot |
| `nautobot_managed_tag` | `avd-managed` | Tag name for AVD-managed objects |
| `nautobot_max_concurrent` | `10` | Maximum concurrent API requests |
| `nautobot_purge` | `false` | Delete ALL objects with managed tag (destructive) |
| `nautobot_purge_prerequisites` | `false` | Also delete prerequisite objects (locations, device types, etc.) when purging |

### Directory Configuration

The role defaults to the standard AVD directory structure:

```yaml
structured_dir: '{{ inventory_dir }}/intended/structured_configs'
```

This default works automatically when running in the standard AVD workflow. Override only if your
structured configs are in a different location.

## Node Type Inference

When `nautobot_node_type_mapping` is not provided, node types are inferred from hostname patterns:

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

The role registers results in the `nautobot_deploy_results` variable:

```yaml
nautobot_deploy_results:
  changed: true
  created: 42
  updated: 10
  skipped: 5
  errors: []
  msg: "Sync completed: 42 created, 10 updated, 5 skipped"
  # When nautobot_return_details is true:
  devices:
    - dc1-spine1
    - dc1-leaf1a
  node_types:
    dc1-spine1: spine
    dc1-leaf1a: l3leaf
  dry_run: false
```

## Nautobot API Token

The role uses Nautobot's Token authentication. Create an API token in Nautobot under
**Admin > API Tokens** and store it securely using Ansible Vault.

## Nautobot vs NetBox Differences

This role is designed specifically for Nautobot, which has several API differences from NetBox:

- **Locations** instead of Sites - Nautobot uses a hierarchical location system with location types
- **Namespaces** for IPAM - IP addresses, prefixes, and VRFs are scoped to namespaces
- **Statuses** are explicit objects with content_types instead of choice fields
- **Roles** are in `/api/extras/roles/` with content_type associations
- **UUIDs** for all object IDs instead of integer IDs

## Dry Run Mode

Use `nautobot_dry_run: true` or Ansible check mode (`--check`) to preview changes:

```yaml
- name: Preview Nautobot changes
  ansible.builtin.import_role:
    name: arista.avd.nautobot_deploy
  vars:
    nautobot_url: "https://nautobot.example.com"
    nautobot_token: "{{ vault_nautobot_token }}"
    nautobot_location_name: "DC1"
    nautobot_dry_run: true
    nautobot_return_details: true
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

- name: Sync to Nautobot
  hosts: localhost
  connection: local
  gather_facts: false
  vars:
    nautobot_url: "https://nautobot.example.com"
    nautobot_token: "{{ vault_nautobot_token }}"
    nautobot_location_name: "DC1"
  tasks:
    - name: Deploy to Nautobot
      ansible.builtin.import_role:
        name: arista.avd.nautobot_deploy
```

> **Note**: The Nautobot sync play uses `hosts: localhost` to avoid inheriting
> network device connection settings from group_vars. The `structured_dir` defaults
> to `{{ inventory_dir }}/intended/structured_configs` which works automatically
> in standard AVD workflows.

## Multi-Site Deployments

For multi-site deployments where devices from different sites are in the same structured configs directory, use `nautobot_location_mapping` to assign devices to locations based on hostname prefix:

```yaml
- name: Sync Multi-DC to Nautobot
  hosts: localhost
  connection: local
  gather_facts: false
  vars:
    nautobot_url: "https://nautobot.example.com"
    nautobot_token: "{{ vault_nautobot_token }}"
    # Map hostname prefixes to Nautobot locations
    nautobot_location_mapping:
      dc1: "DC1_Location"
      dc2: "DC2_Location"
  tasks:
    - name: Deploy to Nautobot
      ansible.builtin.import_role:
        name: arista.avd.nautobot_deploy
```

Devices with hostnames starting with `dc1` (e.g., `dc1-spine1`, `dc1-leaf1a`) will be assigned to "DC1_Location",
while devices starting with `dc2` will be assigned to "DC2_Location".

## Reconciliation (Garbage Collection)

When `nautobot_reconcile: true`, the role will delete objects from Nautobot that are tagged with
`nautobot_managed_tag` (default: `avd-managed`) but no longer exist in the AVD structured configs.

This is useful for cleaning up orphaned objects when devices, interfaces, VLANs, or other
objects are removed from your AVD configurations.

### How It Works

1. All objects synced to Nautobot are automatically tagged with the managed tag (`avd-managed`)
2. During sync, the role tracks which objects were touched
3. At the end of sync (if `nautobot_reconcile: true`), objects with the managed tag that weren't
   touched are deleted

### Deletion Order

Objects are deleted in reverse dependency order to avoid foreign key errors:

1. Cables
2. IP Addresses
3. Interfaces
4. Prefixes
5. VLANs
6. VRFs
7. Devices

### Example with Reconciliation

```yaml
- name: Sync to Nautobot with cleanup
  hosts: localhost
  connection: local
  gather_facts: false
  vars:
    nautobot_url: "https://nautobot.example.com"
    nautobot_token: "{{ vault_nautobot_token }}"
    nautobot_location_name: "DC1"
    nautobot_reconcile: true  # Delete orphaned objects
    # nautobot_managed_tag: "custom-tag"  # Optional: use a custom tag name
  tasks:
    - name: Deploy to Nautobot
      ansible.builtin.import_role:
        name: arista.avd.nautobot_deploy
```

> **Warning**: Use `nautobot_dry_run: true` first to preview what would be deleted before
> enabling reconciliation in production.

## Purge Mode

The `nautobot_purge: true` option deletes **ALL** objects tagged with `nautobot_managed_tag` from Nautobot
without performing any sync. This is useful for:

- Cleaning up a Nautobot instance before migrating to a different source of truth
- Starting fresh when restructuring your AVD configurations
- Removing all AVD-managed data before decommissioning a fabric

### Example: Purge All AVD-Managed Objects

```yaml
- name: Purge AVD objects from Nautobot
  hosts: localhost
  connection: local
  gather_facts: false
  vars:
    nautobot_url: "https://nautobot.example.com"
    nautobot_token: "{{ vault_nautobot_token }}"
    nautobot_purge: true  # Delete ALL avd-managed objects
  tasks:
    - name: Purge Nautobot
      ansible.builtin.import_role:
        name: arista.avd.nautobot_deploy
```

> **Warning**: This is a destructive operation. Use `nautobot_dry_run: true` first to preview
> what would be deleted.

When purge mode is enabled:

- `structured_config_dir` and `location_name`/`location_mapping` are not required
- Objects are deleted in reverse dependency order (cables → IPs → interfaces → etc.)
- Only objects with the managed tag are affected; manually-created objects are preserved

### Purge with Prerequisites

By default, purge mode only deletes main objects (cables, IPs, interfaces, prefixes, VLANs, VRFs, devices).
To also delete prerequisite objects (locations, device types) that support tags,
use `nautobot_purge_prerequisites: true`:

```yaml
- name: Purge everything including prerequisites
  hosts: localhost
  connection: local
  gather_facts: false
  vars:
    nautobot_url: "https://nautobot.example.com"
    nautobot_token: "{{ vault_nautobot_token }}"
    nautobot_purge: true
    nautobot_purge_prerequisites: true  # Also delete locations, device types
  tasks:
    - name: Purge Nautobot completely
      ansible.builtin.import_role:
        name: arista.avd.nautobot_deploy
```

> **Warning**: This removes all traces of AVD from Nautobot, including locations and device types
> that may be shared with other systems.
>
> **Note**: In Nautobot, **manufacturers**, **platforms**, and **roles** do not support tags in the API.
> These prerequisite objects cannot be automatically tracked and will not be deleted during purge.
> They must be manually deleted from Nautobot if cleanup is required.

## Performance

The role uses async HTTP requests with concurrent device processing for high performance.
Multiple devices are synced concurrently, resulting in significantly faster sync times.

### Tuning Concurrency

The `nautobot_max_concurrent` parameter controls how many API requests can be made simultaneously:

- **Lower values** (5-10): Safer for shared Nautobot instances, less server load
- **Higher values** (20-50): Faster sync but may overwhelm the Nautobot server

```yaml
- name: High-performance sync
  ansible.builtin.import_role:
    name: arista.avd.nautobot_deploy
  vars:
    nautobot_url: "https://nautobot.example.com"
    nautobot_token: "{{ vault_nautobot_token }}"
    nautobot_location_name: "DC1"
    nautobot_max_concurrent: 20  # Increase for faster sync
```

## License

Project is published under [Apache 2.0 License](https://github.com/aristanetworks/avd/blob/devel/LICENSE)
