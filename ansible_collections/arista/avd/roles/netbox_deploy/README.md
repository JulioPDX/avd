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

### Directory Configuration

The role uses the same directory structure as other AVD roles:

```yaml
root_dir: '{{ inventory_dir }}'
output_dir: '{{ root_dir }}/intended'
structured_dir: '{{ output_dir }}/structured_configs'
```

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
    root_dir: "{{ playbook_dir }}"
    structured_dir: "{{ root_dir }}/intended/structured_configs"
  tasks:
    - name: Deploy to NetBox
      ansible.builtin.import_role:
        name: arista.avd.netbox_deploy
```

> **Note**: The NetBox sync play uses `hosts: localhost` to avoid inheriting
> network device connection settings from group_vars. Results are automatically
> displayed by the role when `netbox_display_results: true` (default).

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
    root_dir: "{{ playbook_dir }}"
    structured_dir: "{{ root_dir }}/intended/structured_configs"
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

## License

Project is published under [Apache 2.0 License](https://github.com/aristanetworks/avd/blob/devel/LICENSE)
