<!--
  ~ Copyright (c) 2026 Arista Networks, Inc.
  ~ Use of this source code is governed by the Apache License 2.0
  ~ that can be found in the LICENSE file.
  -->

---
name: AVD Schema Verification
description: Validate YAML data against Arista AVD schemas using pyavd
version: "1.0.0"
tags:

- avd
- validation
- schema
- arista
- network-automation

---

# AVD Schema Verification Skill

This skill enables validation of YAML configuration data against Arista AVD (Arista Validated Designs) schemas.

## Overview

AVD uses two primary schemas:

1. **eos_designs** (`avd_design`) - Validates AVD design inputs (fabric topology, node definitions, network services)
2. **eos_cli_config_gen** (`eos_config`) - Validates structured configuration output

## Using pyavd for Validation

### Validate AVD Design Inputs

```python
from pyavd import validate_inputs

# Your AVD design input data
inputs = {
    "fabric_name": "FABRIC",
    "spine": {
        "defaults": {
            "platform": "cEOSLab",
            "loopback_ipv4_pool": "10.255.0.0/27",
            "bgp_as": 65100
        },
        "nodes": [
            {"name": "dc1-spine1", "id": 1, "mgmt_ip": "172.16.1.11/24"}
        ]
    }
}

# Validate against eos_designs schema
result = validate_inputs(inputs)

if result.validation_result.violations:
    print("Validation errors:")
    for violation in result.validation_result.violations:
        print(f"  - {'.'.join(violation.path)}: {violation.message}")
else:
    print("Validation passed!")
    # Access validated/type-converted data
    validated_data = result.validated_data
```

### Validate Structured Configuration

```python
from pyavd import validate_structured_config

# Structured config (output from eos_designs)
structured_config = {
    "hostname": "dc1-spine1",
    "router_bgp": {
        "as": "65100",
        "router_id": "10.255.0.1"
    }
}

result = validate_structured_config(structured_config)

if result.validation_result.violations:
    for violation in result.validation_result.violations:
        print(f"Error at {'.'.join(violation.path)}: {violation.message}")
```

## Validation Result Structure

The `ValidatedDataResult` object contains:

- `validated_data`: Dict with validated/type-converted data (None if validation fails)
- `validation_result`: Contains:
  - `violations`: List of `Violation` objects (empty if validation passed)
  - `deprecations`: List of deprecation warnings

### Violation Object

Each `Violation` object has:

- `path`: List of strings representing the path to the invalid key (e.g., `['spine', 'nodes', '0', 'invalid_key']`)
- `message`: String describing the error (e.g., `"Invalid key."`)

### Checking for Errors

```python
# Check if validation failed
if result.validation_result.violations:
    # Has errors
    pass

# Or check validated_data
if result.validated_data is None:
    # Validation failed
    pass
```

## Common Validation Patterns

### Check for Specific Keys

Before validating, ensure required top-level keys are present:

```python
required_keys = ["fabric_name", "spine", "l3leaf"]
missing = [k for k in required_keys if k not in inputs]
if missing:
    print(f"Missing required keys: {missing}")
```

### Validate with Custom Configuration

```python
from pyavd import validate_inputs
from pyavd_utils.validation import Configuration

# Custom configuration
config = Configuration(warn_eos_config_keys=True)

result = validate_inputs(inputs, configuration=config)
```

## Schema Locations

The AVD schemas are located at:

- **eos_designs**: `python-avd/pyavd/_eos_designs/schema/`
- **eos_cli_config_gen**: `python-avd/pyavd/_eos_cli_config_gen/schema/`

Schema fragments are in the `schema_fragments/` subdirectories.

## Key Schema Sections

### eos_designs Schema Keys

| Key | Description |
| --- | ----------- |
| `fabric_name` | Name of the fabric |
| `spine` | Spine switch definitions |
| `l3leaf` | L3 leaf switch definitions |
| `l2leaf` | L2 leaf switch definitions |
| `tenants` | Network services (VRFs, VLANs, SVIs) |
| `servers` | Connected endpoint definitions |

### eos_cli_config_gen Schema Keys

| Key | Description |
| --- | ----------- |
| `hostname` | Device hostname |
| `router_bgp` | BGP configuration |
| `vlans` | VLAN definitions |
| `vlan_interfaces` | SVI configurations |
| `ethernet_interfaces` | Physical interface configs |

## Error Handling

Always handle validation errors gracefully:

```python
try:
    result = validate_inputs(inputs)
    if result.validation_result.violations:
        # Handle validation errors
        errors = [f"{'.'.join(v.path)}: {v.message}" for v in result.validation_result.violations]
        raise ValueError(f"Validation failed: {errors}")
except ValueError as e:
    print(f"Validation error: {e}")
```

## Best Practices

1. **Always validate before deploying** - Run validation before generating configs
2. **Check deprecation warnings** - Address deprecated keys proactively
3. **Use type hints** - The validated_data is properly typed after validation
4. **Validate incrementally** - Validate each section as you build configs
