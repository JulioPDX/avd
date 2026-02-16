# Copyright (c) 2026 Arista Networks, Inc.
# Use of this source code is governed by the Apache License 2.0
# that can be found in the LICENSE file.

DOCUMENTATION = r"""
---
module: netbox_deploy
version_added: "6.0.0"
author: Arista Ansible Team (@aristanetworks)
short_description: Deploy AVD structured configs to NetBox
description: |-
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

options:
  netbox_url:
    description: URL of the NetBox instance (e.g., "https://netbox.example.com").
    type: str
    required: true
  netbox_token:
    description: |-
      NetBox API token for authentication.
      Supports both v1 tokens (Token xxx) and v2 tokens (nbt_xxx.yyy).
      It is strongly recommended to use Ansible Vault for this.
    type: str
    required: true
  site_name:
    description: Name of the NetBox site to sync devices to. Will be created if it doesn't exist.
    type: str
    required: true
  structured_config_dir:
    description: Path to directory containing AVD structured configuration files.
    type: str
    required: true
  structured_config_suffix:
    description: File suffix for AVD structured configuration files.
    type: str
    default: "yml"
  device_list:
    description: |-
      Optional list of specific devices to sync.
      If not provided, all devices found in structured_config_dir will be synced.
    type: list
    elements: str
    required: false
  node_type_mapping:
    description: |-
      Optional dictionary mapping device hostnames to node types.
      If not provided, node types are inferred from hostname patterns.
      Valid node types: spine, l3leaf, l2leaf, l2spine, l3spine, p, pe, rr, wan_rr, wan_router.
    type: dict
    required: false
  verify_ssl:
    description: Whether to verify SSL certificates when connecting to NetBox.
    type: bool
    default: true
  timeout:
    description: HTTP timeout in seconds for NetBox API calls.
    type: float
    default: 30.0
  create_prerequisites:
    description: |-
      Automatically create prerequisite objects in NetBox (site, device types,
      device roles, manufacturers, platforms) if they don't exist.
    type: bool
    default: true
  dry_run:
    description: |-
      If true, no changes will be made to NetBox.
      The module will report what would be created or updated.
    type: bool
    default: false
  return_details:
    description: |-
      If true, additional details will be returned including the list of devices
      and their node types. May impact performance for large inventories.
    type: bool
    default: false
  fail_on_errors:
    description: |-
      If true, the module will report failure when there are sync errors.
      If false (default), errors are logged but the module reports success,
      allowing playbooks to continue and display results.
    type: bool
    default: false
notes:
  - This module requires the 'httpx' Python library. Install with 'pip install httpx'.
  - |-
    The module supports both NetBox API v1 tokens (legacy format) and v2 tokens
    (format: nbt_xxx.yyy). Token format is auto-detected.
  - |-
    Node types are inferred from hostname patterns:
    - "*spine*" → spine (or l2spine/l3spine if specified in hostname)
    - "*leaf*" ending in "c" → l2leaf
    - "*leaf*" → l3leaf
    - "p*" (not "pe*") → p (MPLS provider)
    - "pe*" → pe (MPLS provider edge)
    - "*rr*" → rr (route reflector)
    - "*wan*rr*" → wan_rr
    - "*wan*" → wan_router
"""

EXAMPLES = r"""
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
"""

RETURN = r"""
changed:
  description: Whether any changes were made to NetBox.
  type: bool
  returned: always
created:
  description: Number of objects created in NetBox.
  type: int
  returned: always
updated:
  description: Number of objects updated in NetBox.
  type: int
  returned: always
skipped:
  description: Number of objects skipped (already up to date).
  type: int
  returned: always
errors:
  description: List of error messages encountered during sync.
  type: list
  elements: str
  returned: always
msg:
  description: Summary message of the sync operation.
  type: str
  returned: always
devices:
  description: List of device hostnames that were synced.
  type: list
  elements: str
  returned: when return_details is true
node_types:
  description: Dictionary mapping hostnames to their detected/assigned node types.
  type: dict
  returned: when return_details is true
dry_run:
  description: Whether the sync was a dry run.
  type: bool
  returned: when return_details is true
"""
