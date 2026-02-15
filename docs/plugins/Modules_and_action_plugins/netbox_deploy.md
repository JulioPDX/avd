---
# This title is used for search results
title: arista.avd.netbox_deploy
---
<!--
  ~ Copyright (c) 2023-2026 Arista Networks, Inc.
  ~ Use of this source code is governed by the Apache License 2.0
  ~ that can be found in the LICENSE file.
  -->

# netbox_deploy

!!! note
    Always use the FQCN (Fully Qualified Collection Name) `arista.avd.netbox_deploy` when using this plugin.

Deploy AVD structured configs to NetBox

## Synopsis

The `arista.avd.netbox_deploy` module is an Ansible Action Plugin that synchronizes
AVD structured configuration data to NetBox DCIM/IPAM models.

The plugin performs the following operations:

- Creates or updates devices in NetBox with platform, serial number, and device role.
- Creates or updates interfaces (Ethernet, Loopback, Management, VLAN) with all settings.
- Creates or updates VLANs and VRFs.
- Creates or updates IP addresses and assigns them to interfaces.
- Creates cable connections between devices based on topology data.

The plugin supports automatic node type inference from hostname patterns or explicit
mapping via the `node_type_mapping` parameter.

## Parameters

| Argument | Type | Required | Default | Value Restrictions | Description |
| -------- | ---- | -------- | ------- | ------------------ | ----------- |
| <samp>netbox_url</samp> | str | True | None | - | URL of the NetBox instance (e.g., &#34;https://netbox.example.com&#34;). |
| <samp>netbox_token</samp> | str | True | None | - | NetBox API token for authentication.<br>Supports both v1 tokens (Token xxx) and v2 tokens (nbt_xxx.yyy).<br>It is strongly recommended to use Ansible Vault for this. |
| <samp>site_name</samp> | str | True | None | - | Name of the NetBox site to sync devices to. Will be created if it doesn&#39;t exist. |
| <samp>structured_config_dir</samp> | str | True | None | - | Path to directory containing AVD structured configuration files. |
| <samp>structured_config_suffix</samp> | str | optional | yml | - | File suffix for AVD structured configuration files. |
| <samp>device_list</samp> | list | False | None | - | Optional list of specific devices to sync.<br>If not provided, all devices found in structured_config_dir will be synced. |
| <samp>node_type_mapping</samp> | dict | False | None | - | Optional dictionary mapping device hostnames to node types.<br>If not provided, node types are inferred from hostname patterns.<br>Valid node types: spine, l3leaf, l2leaf, l2spine, l3spine, p, pe, rr, wan_rr, wan_router. |
| <samp>verify_ssl</samp> | bool | optional | True | - | Whether to verify SSL certificates when connecting to NetBox. |
| <samp>timeout</samp> | float | optional | 30.0 | - | HTTP timeout in seconds for NetBox API calls. |
| <samp>create_prerequisites</samp> | bool | optional | True | - | Automatically create prerequisite objects in NetBox (site, device types,<br>device roles, manufacturers, platforms) if they don&#39;t exist. |
| <samp>dry_run</samp> | bool | optional | False | - | If true, no changes will be made to NetBox.<br>The module will report what would be created or updated. |
| <samp>return_details</samp> | bool | optional | False | - | If true, additional details will be returned including the list of devices<br>and their node types. May impact performance for large inventories. |

## Notes

- This module requires the &#39;httpx&#39; Python library. Install with &#39;pip install httpx&#39;.
- The module supports both NetBox API v1 tokens (legacy format) and v2 tokens
  (format: nbt_xxx.yyy). Token format is auto-detected.
- Node types are inferred from hostname patterns:
  - &#34;*spine*&#34; → spine (or l2spine/l3spine if specified in hostname)
  - &#34;*leaf*&#34; ending in &#34;c&#34; → l2leaf
  - &#34;*leaf*&#34; → l3leaf
  - &#34;p*&#34; (not &#34;pe*&#34;) → p (MPLS provider)
  - &#34;pe*&#34; → pe (MPLS provider edge)
  - &#34;*rr*&#34; → rr (route reflector)
  - &#34;*wan*rr*&#34; → wan_rr
  - &#34;*wan*&#34; → wan_router

## Examples

```yaml
---
- name: Deploy AVD configs to NetBox
  hosts: localhost
  gather_facts: false
  tasks:
    - name: Sync structured configs to NetBox
      arista.avd.netbox_deploy:
        netbox_url: "https://netbox.example.com"
        netbox_token: "{{ netbox_api_token }}"  # Use Ansible Vault
        site_name: "DC1"
        structured_config_dir: "{{ inventory_dir }}/intended/structured_configs"
      register: netbox_result

    - name: Display sync results
      ansible.builtin.debug:
        msg: "Created: {{ netbox_result.created }}, Updated: {{ netbox_result.updated }}"

- name: Sync specific devices with explicit node types
  arista.avd.netbox_deploy:
    netbox_url: "https://netbox.example.com"
    netbox_token: "{{ netbox_api_token }}"
    site_name: "DC1"
    structured_config_dir: "{{ inventory_dir }}/intended/structured_configs"
    device_list:
      - dc1-spine1
      - dc1-spine2
      - dc1-leaf1a
      - dc1-leaf1b
    node_type_mapping:
      dc1-spine1: spine
      dc1-spine2: spine
      dc1-leaf1a: l3leaf
      dc1-leaf1b: l3leaf

- name: Dry run to preview changes
  arista.avd.netbox_deploy:
    netbox_url: "https://netbox.example.com"
    netbox_token: "{{ netbox_api_token }}"
    site_name: "DC1"
    structured_config_dir: "{{ inventory_dir }}/intended/structured_configs"
    dry_run: true
    return_details: true
  register: preview_result
```

## Return Values

| Name | Type | Description |
| ---- | ---- | ----------- |
| changed | bool | Whether any changes were made to NetBox. |
| created | int | Number of objects created in NetBox. |
| updated | int | Number of objects updated in NetBox. |
| skipped | int | Number of objects skipped (already up to date). |
| errors | list | List of error messages encountered during sync. |
| msg | str | Summary message of the sync operation. |
| devices | list | List of device hostnames that were synced. |
| node_types | dict | Dictionary mapping hostnames to their detected/assigned node types. |
| dry_run | bool | Whether the sync was a dry run. |

## Authors

- Arista Ansible Team (@aristanetworks)
