<!--
  ~ Copyright (c) 2026 Arista Networks, Inc.
  ~ Use of this source code is governed by the Apache License 2.0
  ~ that can be found in the LICENSE file.
  -->

---
name: AVD Inventory Construction
description: Construct valid Ansible inventory files for Arista AVD projects
version: "1.0.0"
tags:

- avd
- ansible
- inventory
- arista
- network-automation

---

# AVD Inventory Construction Skill

This skill provides guidance for creating valid Ansible inventory files that work with Arista AVD.

## Inventory Structure Overview

AVD inventories follow a hierarchical group structure:

```yaml
all:
  children:
    FABRIC:              # Top-level fabric group
      children:
        DC1:             # Datacenter/pod group
          children:
            DC1_SPINES:  # Spine switches
            DC1_L3_LEAFS:  # L3 leaf switches
            DC1_L2_LEAFS:  # L2 leaf switches
    NETWORK_SERVICES:    # Services assignment group
      children:
        DC1_L3_LEAFS:
        DC1_L2_LEAFS:
    CONNECTED_ENDPOINTS: # Endpoint assignment group
      children:
        DC1_L3_LEAFS:
        DC1_L2_LEAFS:
```

## Standard Inventory Template

```yaml
---
all:
  children:
    # Main fabric group - contains all network devices
    FABRIC:
      children:
        # Datacenter or pod group
        <DC_NAME>:
          children:
            # Spine switch group
            <DC_NAME>_SPINES:
              hosts:
                <spine1_hostname>:
                  ansible_host: <management_ip>
                <spine2_hostname>:
                  ansible_host: <management_ip>

            # L3 Leaf switch group
            <DC_NAME>_L3_LEAFS:
              hosts:
                <leaf1a_hostname>:
                  ansible_host: <management_ip>
                <leaf1b_hostname>:
                  ansible_host: <management_ip>

            # L2 Leaf switch group (optional)
            <DC_NAME>_L2_LEAFS:
              hosts:
                <l2leaf1_hostname>:
                  ansible_host: <management_ip>

    # Network services group - determines which devices get VRFs/VLANs
    NETWORK_SERVICES:
      children:
        <DC_NAME>_L3_LEAFS:
        <DC_NAME>_L2_LEAFS:

    # Connected endpoints group - determines which devices get server ports
    CONNECTED_ENDPOINTS:
      children:
        <DC_NAME>_L3_LEAFS:
        <DC_NAME>_L2_LEAFS:
```

## Group Variables Directory Structure

Each inventory group should have a corresponding `group_vars` directory:

```text
group_vars/
├── FABRIC/
│   ├── fabric_variables.yml      # Global fabric settings
│   └── fabric_ansible_connectivity.yml  # Ansible connection settings
├── <DC_NAME>/
│   └── dc.yml                    # DC-specific overrides
├── <DC_NAME>_SPINES/
│   └── spines.yml                # Spine definitions
├── <DC_NAME>_L3_LEAFS/
│   └── l3_leafs.yml             # L3 leaf definitions
├── <DC_NAME>_L2_LEAFS/
│   └── l2_leafs.yml             # L2 leaf definitions
├── NETWORK_SERVICES/
│   └── network_services.yml      # VRFs, VLANs, tenants
└── CONNECTED_ENDPOINTS/
    └── connected_endpoints.yml   # Server/endpoint connections
```

## Key Inventory Rules

### 1. Host Naming Convention

- Use lowercase hostnames that match the actual device hostname
- Include datacenter/pod prefix for clarity (e.g., `dc1-spine1`)

### 2. Required Host Variables

Each host needs at minimum:

```yaml
<hostname>:
  ansible_host: <management_ip_without_mask>
```

### 3. Recommended Group Variables for FABRIC

Add connection settings in `group_vars/FABRIC/`:

```yaml
# group_vars/FABRIC/ansible_connectivity.yml
---
ansible_connection: httpapi
ansible_network_os: arista.eos.eos
ansible_become: true
ansible_become_method: enable
ansible_httpapi_use_ssl: true
ansible_httpapi_validate_certs: false

# Authentication (use Ansible Vault for production)
ansible_user: admin
ansible_password: "{{ vault_ansible_password }}"
```

### 4. Group Membership Rules

| Group | Contains | Purpose |
| ----- | -------- | ------- |
| `FABRIC` | All network devices | Global fabric settings |
| `<DC>_SPINES` | Spine switches | Spine-specific node definitions |
| `<DC>_L3_LEAFS` | L3 leaf switches | L3 leaf node definitions |
| `<DC>_L2_LEAFS` | L2 leaf switches | L2 leaf node definitions |
| `NETWORK_SERVICES` | Leaf groups | Assigns VRFs/VLANs to devices |
| `CONNECTED_ENDPOINTS` | Leaf groups | Assigns server ports to devices |

### 5. Multi-DC Inventory

For multi-datacenter deployments:

```yaml
all:
  children:
    FABRIC:
      children:
        DC1:
          children:
            DC1_SPINES:
            DC1_L3_LEAFS:
        DC2:
          children:
            DC2_SPINES:
            DC2_L3_LEAFS:
    NETWORK_SERVICES:
      children:
        DC1_L3_LEAFS:
        DC2_L3_LEAFS:
```

## Validation Checklist

Before using the inventory:

- [ ] All hostnames are unique across the fabric
- [ ] All `ansible_host` IPs are reachable management IPs
- [ ] Spine groups contain only spine switches
- [ ] Leaf groups contain only leaf switches
- [ ] `NETWORK_SERVICES` includes all devices that need VRFs/VLANs
- [ ] `CONNECTED_ENDPOINTS` includes all devices with server connections
- [ ] Group names match the `group_vars` directory names exactly
- [ ] `fabric_name` in `group_vars/FABRIC/` matches an inventory group name

## Common Mistakes

1. **Missing group in NETWORK_SERVICES** - VLANs won't be configured
2. **Incorrect hostname** - Must match the `name` in node definitions
3. **IP with subnet mask** - `ansible_host` should not include `/24`
4. **Case sensitivity** - Group names are case-sensitive
5. **fabric_name mismatch** - The `fabric_name` variable must match an existing Ansible group name (typically `FABRIC`)
