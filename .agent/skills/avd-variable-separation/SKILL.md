<!--
  ~ Copyright (c) 2026 Arista Networks, Inc.
  ~ Use of this source code is governed by the Apache License 2.0
  ~ that can be found in the LICENSE file.
  -->

---
name: AVD Variable Separation
description: Logically separate AVD input variables into organized YAML files
version: "1.0.0"
tags:

- avd
- yaml
- organization
- arista
- network-automation

---

# AVD Variable Separation Skill

This skill provides guidance for organizing AVD input variables into logical, maintainable YAML files.

## Variable Separation Philosophy

AVD variables should be separated by:

1. **Scope** - Global fabric vs. node-type specific
2. **Function** - Fabric topology vs. network services vs. endpoints
3. **Change frequency** - Stable infrastructure vs. dynamic services

## Standard File Organization

### FABRIC Group Variables

**`group_vars/FABRIC/fabric_variables.yml`** - Global fabric settings:

> **IMPORTANT**: The `fabric_name` value MUST match an existing Ansible group name in your inventory
> that contains all fabric hosts. This is validated by AVD at runtime.

```yaml
---
# fabric_name must match an Ansible group name in inventory (e.g., FABRIC)
fabric_name: FABRIC

# Routing protocols
underlay_routing_protocol: ebgp
overlay_routing_protocol: ebgp

# Documentation settings
eos_designs_documentation:
  topology_csv: true
  p2p_links_csv: true

# AAA/Local users
aaa_settings:
  local_users:
    - name: admin
      privilege: 15
      role: network-admin
      no_password: true

# BGP peer group settings
bgp_peer_groups:
  evpn_overlay_peers:
    password: <encrypted>
  ipv4_underlay_peers:
    password: <encrypted>

# Interface defaults
p2p_uplinks_mtu: 9214

# Default interface mappings per node type
default_interfaces:
  - types: [spine]
    platforms: [default]
    uplink_interfaces: [Ethernet1-2]
    downlink_interfaces: [Ethernet1-8]
  - types: [l3leaf]
    platforms: [default]
    uplink_interfaces: [Ethernet1-2]
    mlag_interfaces: [Ethernet3-4]

# Management settings
dns_settings:
  servers:
    - ip_address: 192.168.1.1

ntp_settings:
  server_vrf: use_mgmt_interface_vrf
  servers:
    - name: 0.pool.ntp.org
```

### Spine Variables

**`group_vars/<DC>_SPINES/spines.yml`** - Spine node definitions:

```yaml
---
type: spine

spine:
  defaults:
    platform: <platform_model>
    loopback_ipv4_pool: 10.255.0.0/27
    bgp_as: 65100

  nodes:
    - name: dc1-spine1
      id: 1
      mgmt_ip: 172.16.1.11/24
    - name: dc1-spine2
      id: 2
      mgmt_ip: 172.16.1.12/24
```

### L3 Leaf Variables

**`group_vars/<DC>_L3_LEAFS/l3_leafs.yml`** - L3 leaf definitions:

```yaml
---
type: l3leaf

l3leaf:
  defaults:
    platform: <platform_model>
    loopback_ipv4_pool: 10.255.0.0/27
    loopback_ipv4_offset: 2  # Offset to avoid spine loopback overlap
    vtep_loopback_ipv4_pool: 10.255.1.0/27
    uplink_switches: ['dc1-spine1', 'dc1-spine2']
    uplink_ipv4_pool: 10.255.255.0/26
    mlag_peer_ipv4_pool: 10.255.1.64/27
    mlag_peer_l3_ipv4_pool: 10.255.1.96/27
    virtual_router_mac_address: 00:1c:73:00:00:99
    spanning_tree_priority: 4096
    spanning_tree_mode: mstp

  node_groups:
    - group: DC1_L3_LEAF1
      bgp_as: 65101
      nodes:
        - name: dc1-leaf1a
          id: 1
          mgmt_ip: 172.16.1.101/24
        - name: dc1-leaf1b
          id: 2
          mgmt_ip: 172.16.1.102/24
```

### L2 Leaf Variables

**`group_vars/<DC>_L2_LEAFS/l2_leafs.yml`** - L2 leaf definitions:

```yaml
---
type: l2leaf

l2leaf:
  defaults:
    platform: <platform_model>
    spanning_tree_mode: mstp

  node_groups:
    - group: DC1_L2_LEAF1
      uplink_switches: [dc1-leaf1a, dc1-leaf1b]
      nodes:
        - name: dc1-leaf1c
          id: 1
          mgmt_ip: 172.16.1.151/24
```

### Network Services Variables

**`group_vars/NETWORK_SERVICES/network_services.yml`** - Tenants, VRFs, VLANs:

```yaml
---
tenants:
  - name: TENANT1
    mac_vrf_vni_base: 10000
    vrfs:
      - name: VRF10
        vrf_vni: 10
        vtep_diagnostic:
          loopback: 10
          loopback_ip_range: 10.255.10.0/27
        svis:
          - id: 11
            name: VRF10_VLAN11
            enabled: true
            ip_address_virtual: 10.10.11.1/24

    l2vlans:
      - id: 3401
        name: L2_VLAN3401
```

### Connected Endpoints Variables

**`group_vars/CONNECTED_ENDPOINTS/connected_endpoints.yml`** - Server connections:

```yaml
---
servers:
  - name: dc1-leaf1-server1
    adapters:
      - endpoint_ports: [PCI1, PCI2]
        switch_ports: [Ethernet5, Ethernet5]
        switches: [dc1-leaf1a, dc1-leaf1b]
        vlans: 11-12,21-22
        native_vlan: 4092
        mode: trunk
        spanning_tree_portfast: edge
        port_channel:
          mode: active
```

## Variable Separation Rules

| Variable Type | File Location | Example Keys |
| ------------- | ------------- | ------------ |
| Global fabric settings | `FABRIC/fabric_variables.yml` | `fabric_name`, `bgp_peer_groups` |
| Spine definitions | `<DC>_SPINES/spines.yml` | `type: spine`, `spine:` |
| L3 leaf definitions | `<DC>_L3_LEAFS/l3_leafs.yml` | `type: l3leaf`, `l3leaf:` |
| L2 leaf definitions | `<DC>_L2_LEAFS/l2_leafs.yml` | `type: l2leaf`, `l2leaf:` |
| Network services | `NETWORK_SERVICES/network_services.yml` | `tenants`, `vrfs`, `svis` |
| Server connections | `CONNECTED_ENDPOINTS/connected_endpoints.yml` | `servers`, `adapters` |

## Common Schema Pitfalls

Avoid these common mistakes when writing AVD variables:

| Wrong | Correct |
| ----- | ------- |
| `dns_settings.name_servers: [8.8.8.8]` | `dns_settings.servers: [{ip_address: 8.8.8.8}]` |
| `ntp_settings.servers: [0.pool.ntp.org]` | `ntp_settings.servers: [{name: 0.pool.ntp.org}]` |

### dns_settings Format

```yaml
# WRONG - will fail schema validation
dns_settings:
  name_servers:
    - 8.8.8.8

# CORRECT
dns_settings:
  servers:
    - ip_address: 8.8.8.8
      vrf: MGMT  # optional
```

### ntp_settings Format

```yaml
# CORRECT
ntp_settings:
  server_vrf: use_mgmt_interface_vrf
  servers:
    - name: 0.pool.ntp.org
```

## Best Practices

1. **One node type per file** - Don't mix spine and leaf in same file
2. **Use defaults wisely** - Put common settings in `defaults:` section
3. **Consistent naming** - Match inventory group names to directory names
4. **Comment liberally** - Document IP pools, ASN assignments, design choices
5. **Validate after changes** - Use `validate_inputs()` after modifications
