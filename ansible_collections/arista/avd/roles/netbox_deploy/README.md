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
| `netbox_site_name` | Name of the site in NetBox |

### Optional Variables

| Variable | Default | Description |
| -------- | ------- | ----------- |
| `netbox_verify_ssl` | `true` | Verify SSL certificates |
| `netbox_timeout` | `30.0` | HTTP timeout in seconds |
| `netbox_create_prerequisites` | `true` | Auto-create site, device types, etc. |
| `netbox_dry_run` | `false` | Preview changes without applying |
| `netbox_devices` | All devices | List of specific devices to sync |
| `netbox_node_type_mapping` | Auto-inferred | Explicit hostname to node type mapping |
| `netbox_return_details` | `false` | Return detailed sync information |

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
- name: Build and Deploy
  hosts: FABRIC
  tasks:
    - name: Generate AVD structured configs
      ansible.builtin.import_role:
        name: arista.avd.eos_designs

    - name: Generate EOS configs
      ansible.builtin.import_role:
        name: arista.avd.eos_cli_config_gen

    - name: Deploy to NetBox
      ansible.builtin.import_role:
        name: arista.avd.netbox_deploy
      vars:
        netbox_url: "https://netbox.example.com"
        netbox_token: "{{ vault_netbox_token }}"
        netbox_site_name: "{{ fabric_name }}"
```

## License

Project is published under [Apache 2.0 License](https://github.com/aristanetworks/avd/blob/devel/LICENSE)
